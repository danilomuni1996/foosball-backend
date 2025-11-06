from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select, SQLModel
from typing import Optional, List, Dict
from pathlib import Path
from models import Player, Match
from db import init_db, engine  # db.py configurato con pool_pre_ping e sslmode=require
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
    # Evita accesso DB in avvio (Neon può essere idle/saturo). Se serve, chiama init_db(lazy=False) da endpoint admin.
    pass

def get_session():
    with Session(engine) as session:
        yield session

@app.get("/")
def root():
    return {"ok": True}

# Healthcheck che non fa fallire il pod
@app.get("/healthz")
def healthz(session: Session = Depends(get_session)):
    try:
        session.exec(text("SELECT 1"))
        return {"ok": True, "db": "up"}
    except Exception:
        return {"ok": True, "db": "down"}

# ========== UTILS RICALCOLO PUNTI SU PLAYERS ==========

def recompute_players_points_tx(session: Session):
    """
    Ricostruisce Player.points in base alle partite presenti in matches,
    applicando la regola:
      - cappotto 6-0: vincenti +4, perdenti -1
      - vittoria normale: vincenti +3
      - sconfitta normale: +1 ai perdenti
    """
    # Assicurati che la colonna points esista (eseguire una tantum se manca):
    # ALTER TABLE players ADD COLUMN points INTEGER NOT NULL DEFAULT 0;

    # 1) azzera
    session.exec(text("UPDATE players SET points = 0"))

    # 2) cappotto 6-0 vinto da Team A: +4 ai due di A, -1 ai due di B
    session.exec(text("""
        UPDATE players p
        SET points = p.points + 4
        FROM matches m
        WHERE m.score_a = 6 AND m.score_b = 0
          AND p.id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id)
    """))
    session.exec(text("""
        UPDATE players p
        SET points = p.points - 1
        FROM matches m
        WHERE m.score_a = 6 AND m.score_b = 0
          AND p.id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id)
    """))

    # 3) cappotto 0-6 vinto da Team B: +4 ai due di B, -1 ai due di A
    session.exec(text("""
        UPDATE players p
        SET points = p.points + 4
        FROM matches m
        WHERE m.score_b = 6 AND m.score_a = 0
          AND p.id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id)
    """))
    session.exec(text("""
        UPDATE players p
        SET points = p.points - 1
        FROM matches m
        WHERE m.score_b = 6 AND m.score_a = 0
          AND p.id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id)
    """))

    # 4) vittorie normali: +3 ai vincenti
    session.exec(text("""
        UPDATE players p
        SET points = p.points + 3
        FROM matches m
        WHERE m.score_a > m.score_b
          AND p.id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id)
    """))
    session.exec(text("""
        UPDATE players p
        SET points = p.points + 3
        FROM matches m
        WHERE m.score_b > m.score_a
          AND p.id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id)
    """))

    # 5) sconfitte normali: +1 ai perdenti
    session.exec(text("""
        UPDATE players p
        SET points = p.points + 1
        FROM matches m
        WHERE m.score_a < m.score_b
          AND p.id IN (m.teamA_attacker_id, m.teamA_goalkeeper_id)
    """))
    session.exec(text("""
        UPDATE players p
        SET points = p.points + 1
        FROM matches m
        WHERE m.score_b < m.score_a
          AND p.id IN (m.teamB_attacker_id, m.teamB_goalkeeper_id)
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

# ========== LEADERBOARD ==========

@app.get("/leaderboard", response_model=List[Player])
def leaderboard(session: Session = Depends(get_session)):
    # legge direttamente da players ordinando per points
    return session.exec(select(Player).order_by(Player.points.desc(), Player.name.asc())).all()

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

    # opzionale: puoi rimuovere gli incrementi immediati se preferisci solo il ricalcolo globale
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

    # ricalcolo completo opzionale anche qui (puoi commentare se tieni gli incrementi immediati)
    # try:
    #     recompute_players_points_tx(session)
    #     session.commit()
    # except Exception as e:
    #     session.rollback()
    #     print("ERROR recompute after create_match:", repr(e))

    return m

@app.delete("/matches/{match_id}")
def delete_match(match_id: int, session: Session = Depends(get_session)):
    m = session.get(Match, match_id)
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    session.delete(m)
    session.commit()

    # DOPO cancellazione ricalcola sempre per rimuovere l’effetto del match eliminato
    try:
        recompute_players_points_tx(session)
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

    # azzera i punti dei giocatori (nessuna partita rimasta)
    session.exec(text("UPDATE players SET points = 0"))
    session.commit()

    return {"players": session.exec(select(func.count(Player.id))).one(), "matches": 0}

# Endpoint per ricalcolo manuale completo (utile dopo import massivi)
@app.post("/admin/recompute-leaderboard")
def recompute_leaderboard(session: Session = Depends(get_session)):
    try:
        recompute_players_points_tx(session)
        session.commit()
        return {"status": "ok"}
    except Exception as e:
        session.rollback()
        print("ERROR recompute (manual):", repr(e))
        raise HTTPException(status_code=500, detail="Recompute failed")
