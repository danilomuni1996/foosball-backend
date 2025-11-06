from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select, SQLModel
from typing import Optional, List, Dict
from pathlib import Path
from models import Player, Match
from db import init_db, engine
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
    init_db()

def get_session():
    with Session(engine) as session:
        yield session

@app.get("/")
def root():
    return {"ok": True}

# --------- helper: tabella punteggi materializzata ---------

def ensure_scores_table(session: Session):
    # Crea tabella players_scores se non esiste e inserisce le righe per i giocatori mancanti
    session.exec(text("""
        CREATE TABLE IF NOT EXISTS players_scores (
            player_id INTEGER PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
            points INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    session.exec(text("""
        INSERT INTO players_scores (player_id, points, updated_at)
        SELECT p.id, 0, NOW()
        FROM players p
        ON CONFLICT (player_id) DO NOTHING
    """))
    session.commit()

def recompute_scores_tx(session: Session):
    # Ricostruisce i punteggi da zero in una transazione aperta
    ensure_scores_table(session)

    # azzera
    session.exec(text("UPDATE players_scores SET points = 0, updated_at = NOW()"))

    # cappotto 6-0 vinto da Team A: +4 ai due di A, -1 ai due di B
    session.exec(text("""
        UPDATE players_scores ps
        SET points = ps.points + 4, updated_at = NOW()
        FROM matches m
        WHERE m.score_a = 6 AND m.score_b = 0
          AND ps.player_id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id)
    """))
    session.exec(text("""
        UPDATE players_scores ps
        SET points = ps.points - 1, updated_at = NOW()
        FROM matches m
        WHERE m.score_a = 6 AND m.score_b = 0
          AND ps.player_id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id)
    """))

    # cappotto 0-6 vinto da Team B: +4 ai due di B, -1 ai due di A
    session.exec(text("""
        UPDATE players_scores ps
        SET points = ps.points + 4, updated_at = NOW()
        FROM matches m
        WHERE m.score_b = 6 AND m.score_a = 0
          AND ps.player_id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id)
    """))
    session.exec(text("""
        UPDATE players_scores ps
        SET points = ps.points - 1, updated_at = NOW()
        FROM matches m
        WHERE m.score_b = 6 AND m.score_a = 0
          AND ps.player_id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id)
    """))

    # vittorie normali: +3 ai vincenti
    session.exec(text("""
        UPDATE players_scores ps
        SET points = ps.points + 3, updated_at = NOW()
        FROM matches m
        WHERE m.score_a > m.score_b
          AND ps.player_id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id)
    """))
    session.exec(text("""
        UPDATE players_scores ps
        SET points = ps.points + 3, updated_at = NOW()
        FROM matches m
        WHERE m.score_b > m.score_a
          AND ps.player_id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id)
    """))

    # sconfitte normali: +1 ai perdenti
    session.exec(text("""
        UPDATE players_scores ps
        SET points = ps.points + 1, updated_at = NOW()
        FROM matches m
        WHERE m.score_a < m.score_b
          AND ps.player_id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id)
    """))
    session.exec(text("""
        UPDATE players_scores ps
        SET points = ps.points + 1, updated_at = NOW()
        FROM matches m
        WHERE m.score_b < m.score_a
          AND ps.player_id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id)
    """))

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

    # assicurati che esista una riga in players_scores
    ensure_scores_table(session)
    session.exec(text("""
        INSERT INTO players_scores (player_id, points, updated_at)
        VALUES (:pid, 0, NOW())
        ON CONFLICT (player_id) DO NOTHING
    """), {"pid": p.id})
    session.commit()

    return p

@app.get("/players", response_model=List[Player])
def list_players(session: Session = Depends(get_session)):
    return session.exec(select(Player)).all()

@app.delete("/players/{player_id}")
def delete_player_by_id(player_id: int, session: Session = Depends(get_session)):
    p = session.get(Player, player_id)
    if not p:
        raise HTTPException(status_code=404, detail="Player not found")
    if getattr(p, "photo_url", None):
        filename = p.photo_url.replace("/static/", "")
        path = UPLOAD_DIR / filename
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
    session.delete(p)
    session.commit()
    return {"status": "ok", "deleted": {"id": p.id, "name": p.name}}

@app.delete("/players/by-name")
def delete_player_by_name(
    name: str,
    like: bool = False,
    session: Session = Depends(get_session)
):
    cond = func.lower(Player.name) == name.lower() if not like \
        else func.lower(Player.name).like(f"%{name.lower()}%")
    players = session.exec(select(Player).where(cond)).all()
    if not players:
        raise HTTPException(status_code=404, detail="Player not found")
    if len(players) > 1:
        raise HTTPException(
            status_code=409,
            detail={"multiple": [{"id": p.id, "name": p.name} for p in players]}
        )
    p = players[0]
    if getattr(p, "photo_url", None):
        filename = p.photo_url.replace("/static/", "")
        path = UPLOAD_DIR / filename
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
    session.delete(p)
    session.commit()
    return {"status": "ok", "deleted": {"id": p.id, "name": p.name}}

# ========== LEADERBOARD (materializzata) ==========

@app.get("/leaderboard")
def leaderboard(session: Session = Depends(get_session)):
    ensure_scores_table(session)
    rows = session.exec(text("""
        SELECT p.id, p.name, p.photo_url, COALESCE(ps.points, 0) AS points
        FROM players p
        LEFT JOIN players_scores ps ON ps.player_id = p.id
        ORDER BY points DESC, p.name ASC
    """)).all()
    return [{"id": r[0], "name": r[1], "photo_url": r[2], "points": int(r[3])} for r in rows]

@app.post("/admin/recompute-leaderboard")
def recompute_leaderboard(session: Session = Depends(get_session)):
    try:
        recompute_scores_tx(session)
        session.commit()
        return {"status": "ok"}
    except Exception as e:
        session.rollback()
        print("ERROR recompute:", repr(e))
        raise HTTPException(status_code=500, detail="Recompute failed")

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

    # (facoltativo) mantieni anche Player.points se vuoi, ma la classifica non lo usa:
    for pid, pts in awarded.items():
        players[pid].points += pts
    session.add_all(players.values())

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

    # ricalcola classifica materializzata
    try:
        recompute_scores_tx(session)
        session.commit()
    except Exception as e:
        session.rollback()
        print("ERROR recompute after create_match:", repr(e))

    return m

@app.delete("/matches/{match_id}")
def delete_match(match_id: int, session: Session = Depends(get_session)):
    m = session.get(Match, match_id)
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    session.delete(m)
    session.commit()

    # ricalcola classifica materializzata
    try:
        recompute_scores_tx(session)
        session.commit()
    except Exception as e:
        session.rollback()
        print("ERROR recompute after delete_match:", repr(e))

    return {"status": "ok"}

# ========== ADMIN ==========

@app.post("/admin/reset")
def admin_reset(session: Session = Depends(get_session)):
    # elimina tutte le partite
    for m in session.exec(select(Match)).all():
        session.delete(m)
    session.commit()

    # opzionale: azzera i punti materializzati e riallinea tabella punteggi
    ensure_scores_table(session)
    session.exec(text("UPDATE players_scores SET points = 0, updated_at = NOW()"))
    session.commit()

    return {"players": session.exec(select(func.count(Player.id))).one(), "matches": 0}
