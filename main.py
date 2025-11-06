from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select, SQLModel
from typing import Optional, List, Dict
from pathlib import Path
from models import Player, Match
from db import init_db, engine  # engine resiliente (pool_pre_ping, sslmode=require) e init lazy
import json
from sqlalchemy import func, text
from datetime import datetime

app = FastAPI(title="Foosball API")

# CORS (sviluppo)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(UPLOAD_DIR)), name="static")

@app.on_event("startup")
def on_startup():
    # Evita accesso DB in avvio (Neon può essere idle/saturo)
    pass

def get_session():
    with Session(engine) as session:
        yield session

@app.get("/")
def root():
    return {"ok": True}

# Healthcheck
@app.get("/healthz")
def healthz(session: Session = Depends(get_session)):
    try:
        session.exec(text("SELECT 1"))
        return {"ok": True, "db": "up"}
    except Exception:
        return {"ok": True, "db": "down"}

# ========== HELPERS punteggio ON-DEMAND ==========

ON_DEMAND_SQL = text("""
    SELECT
      p.id,
      p.name,
      p.photo_url,
      COALESCE(SUM(
        CASE
          -- cappotto 6-0 vinto da Team A: +4 ai due di A, -1 ai due di B
          WHEN m.score_a = 6 AND m.score_b = 0 AND p.id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id) THEN 4
          WHEN m.score_a = 6 AND m.score_b = 0 AND p.id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id) THEN -1

          -- cappotto 0-6 vinto da Team B: +4 ai due di B, -1 ai due di A
          WHEN m.score_b = 6 AND m.score_a = 0 AND p.id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id) THEN 4
          WHEN m.score_b = 6 AND m.score_a = 0 AND p.id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id) THEN -1

          -- vittoria normale: +3 ai vincenti
          WHEN m.score_a > m.score_b AND p.id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id) THEN 3
          WHEN m.score_b > m.score_a AND p.id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id) THEN 3

          -- sconfitta normale: +1 ai perdenti
          WHEN m.score_a < m.score_b AND p.id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id) THEN 1
          WHEN m.score_b < m.score_a AND p.id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id) THEN 1

          -- pareggi: 0 (se vuoi +1 a tutti, aggiungi WHEN m.score_a = m.score_b ...)
          ELSE 0
        END
      ), 0) AS points
    FROM players p
    LEFT JOIN matches m
      ON p.id IN (
        m.teamA_attacker_id, m.teamA_goalkeeper_id,
        m.teamB_attacker_id, m.teamB_goalkeeper_id
      )
    GROUP BY p.id, p.name, p.photo_url
""")

def fetch_players_with_points(session: Session):
    rows = session.exec(ON_DEMAND_SQL).all()
    return [{"id": r[0], "name": r[1], "photo_url": r[2], "points": int(r[3])} for r in rows]

# ========== PLAYERS ==========

@app.post("/players", response_model=Player)
async def create_player(
    name: str = Form(...),
    preferred_role: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session),
):
    photo_url = None
    if photo:
        dest = UPLOAD_DIR / photo.filename
        with dest.open("wb") as f:
            f.write(await photo.read())
        photo_url = f"/static/{photo.filename}"
    p = Player(name=name, preferred_role=preferred_role, photo_url=photo_url)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p

# Restituisce i player con points calcolati ON-DEMAND (non il campo persistito)
@app.get("/players")
def list_players_with_points(session: Session = Depends(get_session)):
    try:
        data = fetch_players_with_points(session)
        # Ordina per nome o per points a tua scelta; qui per nome
        data.sort(key=lambda x: (x["name"] or "").lower())
        return data
    except Exception as e:
        print("ERROR /players (on-demand):", repr(e))
        raise HTTPException(status_code=500, detail="Players query failed")

# ========== LEADERBOARD ==========

# Classifica on-demand: calcolo diretto dai match
@app.get("/leaderboard")
def leaderboard(session: Session = Depends(get_session)):
    try:
        data = fetch_players_with_points(session)
        # Ordina per points DESC e poi per nome
        data.sort(key=lambda x: (-x["points"], (x["name"] or "").lower()))
        return data
    except Exception as e:
        print("ERROR /leaderboard (on-demand):", repr(e))
        raise HTTPException(status_code=500, detail="Leaderboard query failed")

# ========== MATCHES ==========

class MatchIn(SQLModel):
    teamA_attacker_id: int
    teamA_goalkeeper_id: int
    teamB_attacker_id: int
    teamB_goalkeeper_id: int
    score_a: int
    score_b: int

@app.get("/matches", response_model=List[Match])
def list_matches(session: Session = Depends(get_session)):
    return session.exec(select(Match).order_by(Match.created_at.desc())).all()

@app.post("/matches", response_model=Match)
def create_match(data: MatchIn, session: Session = Depends(get_session)):
    ids = [
        data.teamA_attacker_id, data.teamA_goalkeeper_id,
        data.teamB_attacker_id, data.teamB_goalkeeper_id,
    ]
    players = {
        p.id: p
        for p in session.exec(select(Player).where(Player.id.in_(ids))).all()
    }
    if len(players) != 4:
        raise HTTPException(status_code=400, detail="Giocatori non validi")

    winner = "A" if data.score_a > data.score_b else "B"
    teamA = [players[data.teamA_attacker_id], players[data.teamA_goalkeeper_id]]
    teamB = [players[data.teamB_attacker_id], players[data.teamB_goalkeeper_id]]

    cappotto = (data.score_a == 6 and data.score_b == 0) or (data.score_a == 0 and data.score_b == 6)
    awarded: Dict[int, int] = {}

    if winner == "A":
        win_team, lose_team = teamA, teamB
    else:
        win_team, lose_team = teamB, teamA

    if cappotto:
        for p in win_team:
            awarded[p.id] = 4
        for p in lose_team:
            awarded[p.id] = -1
    else:
        for p in win_team:
            awarded[p.id] = 3
        for p in lose_team:
            awarded[p.id] = 1

    # Non è necessario aggiornare Player.points, perché la classifica è on-demand
    m = Match(
        teamA_attacker_id=data.teamA_attacker_id,
        teamA_goalkeeper_id=data.teamA_goalkeeper_id,
        teamB_attacker_id=data.teamB_attacker_id,
        teamB_goalkeeper_id=data.teamB_goalkeeper_id,
        score_a=data.score_a,
        score_b=data.score_b,
        winner_team=winner,
        points_awarded=json.dumps(awarded),
        created_at=datetime.utcnow(),
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    return m

@app.delete("/matches/{match_id}")
def delete_match(match_id: int, session: Session = Depends(get_session)):
    m = session.get(Match, match_id)
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    session.delete(m)
    session.commit()
    # Nessun ricalcolo necessario: la classifica/players è on-demand
    return {"status": "ok"}

# ========== ADMIN ==========

@app.post("/admin/reset")
def admin_reset(session: Session = Depends(get_session)):
    # elimina tutte le partite
    for m in session.exec(select(Match)).all():
        session.delete(m)
    session.commit()
    # nessun reset punti necessario: calcolo on-demand
    return {"players": session.exec(select(func.count(Player.id))).one(), "matches": 0}
