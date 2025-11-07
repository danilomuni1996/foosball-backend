from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select, SQLModel
from typing import Optional, List, Dict
from pathlib import Path
from models import Player, Match
from db import engine
import json
from sqlalchemy import func, text
from datetime import datetime

app = FastAPI(title="Foosball API")

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
    pass

def get_session():
    with Session(engine) as session:
        yield session

@app.get("/")
def root():
    return {"ok": True}

@app.get("/healthz")
def healthz(session: Session = Depends(get_session)):
    try:
        session.exec(text("SELECT 1"))
        return {"ok": True, "db": "up"}
    except Exception:
        return {"ok": True, "db": "down"}

# ========== UTILS RICALCOLO PUNTI (CON NOMI COLONNE MINUSCOLI) ==========
def recompute_players_points_tx(session: Session):
    """
    Ricostruisce i punti dei giocatori con una singola, efficiente query SQL,
    usando i nomi delle colonne corretti (minuscoli).
    """
    recompute_query = text("""
        WITH player_points AS (
            SELECT
                player_id,
                CASE
                    WHEN (team = 'A' AND score_a = 6 AND score_b = 0) OR (team = 'B' AND score_b = 6 AND score_a = 0) THEN 4
                    WHEN (team = 'A' AND score_a > score_b) OR (team = 'B' AND score_b > score_a) THEN 3
                    WHEN (team = 'A' AND score_b = 6 AND score_a = 0) OR (team = 'B' AND score_a = 6 AND score_b = 0) THEN -1
                    ELSE 1
                END AS points
            FROM (
                SELECT id, teama_attacker_id AS player_id, 'A' AS team, score_a, score_b FROM match UNION ALL
                SELECT id, teama_goalkeeper_id AS player_id, 'A' AS team, score_a, score_b FROM match UNION ALL
                SELECT id, teamb_attacker_id AS player_id, 'B' AS team, score_a, score_b FROM match UNION ALL
                SELECT id, teamb_goalkeeper_id AS player_id, 'B' AS team, score_a, score_b FROM match
            ) AS unnested_matches
        ),
        total_points AS (
            SELECT player_id, SUM(points) AS total
            FROM player_points
            GROUP BY player_id
        )
        UPDATE player p
        SET points = COALESCE(tp.total, 0)
        FROM total_points tp
        WHERE p.id = tp.player_id;
    """)
    
    # Azzera i punti di tutti prima di ricalcolare
    session.exec(text("UPDATE player SET points = 0"))
    
    # Esegui la query di ricalcolo
    session.exec(recompute_query)

# ========== PLAYERS (invariato) ==========
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

# ========== LEADERBOARD ==========
@app.get("/leaderboard", response_model=List[Player])
def leaderboard(session: Session = Depends(get_session)):
    try:
        recompute_players_points_tx(session)
        session.commit()
        return session.exec(select(Player).order_by(Player.points.desc(), Player.name.asc())).all()
    except Exception as e:
        session.rollback()
        print(f"FATAL ERROR on /leaderboard: {repr(e)}")
        raise HTTPException(status_code=500, detail="Failed to recompute leaderboard.")

# ========== MATCHES (CON NOMI COLONNE MINUSCOLI) ==========
class MatchIn(SQLModel):
    teama_attacker_id: int
    teama_goalkeeper_id: int
    teamb_attacker_id: int
    teamb_goalkeeper_id: int
    score_a: int
    score_b: int

@app.get("/matches", response_model=List[Match])
def list_matches(session: Session = Depends(get_session)):
    return session.exec(select(Match).order_by(Match.created_at.desc())).all()

@app.post("/matches", response_model=Match)
def create_match(data: MatchIn, session: Session = Depends(get_session)):
    ids = [
        data.teama_attacker_id, data.teama_goalkeeper_id,
        data.teamb_attacker_id, data.teamb_goalkeeper_id,
    ]
    players_check = session.exec(select(Player).where(Player.id.in_(ids))).all()
    if len(players_check) != 4:
        raise HTTPException(status_code=400, detail="Giocatori non validi")

    winner = "A" if data.score_a > data.score_b else "B"
    cappotto = (data.score_a == 6 and data.score_b == 0) or (data.score_a == 0 and data.score_b == 6)
    awarded = {}
    
    if winner == "A":
        awarded.update({p_id: (4 if cappotto else 3) for p_id in [data.teama_attacker_id, data.teama_goalkeeper_id]})
        awarded.update({p_id: (-1 if cappotto else 1) for p_id in [data.teamb_attacker_id, data.teamb_goalkeeper_id]})
    else:
        awarded.update({p_id: (4 if cappotto else 3) for p_id in [data.teamb_attacker_id, data.teamb_goalkeeper_id]})
        awarded.update({p_id: (-1 if cappotto else 1) for p_id in [data.teama_attacker_id, data.teama_goalkeeper_id]})

    m = Match(
        teama_attacker_id=data.teama_attacker_id,
        teama_goalkeeper_id=data.teama_goalkeeper_id,
        teamb_attacker_id=data.teamb_attacker_id,
        teamb_goalkeeper_id=data.teamb_goalkeeper_id,
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
    # Il ricalcolo non è necessario qui, avverrà alla prossima chiamata a /leaderboard
    return {"status": "ok"}

# ========== ADMIN (assicurati che usi i nomi corretti) ==========
@app.post("/admin/reset")
def admin_reset(session: Session = Depends(get_session)):
    session.exec(text("DELETE FROM match"))
    session.exec(text("UPDATE player SET points = 0"))
    session.commit()
    return {"players": session.exec(select(func.count(Player.id))).one(), "matches": 0}

@app.post("/admin/recompute-leaderboard")
def recompute_leaderboard(session: Session = Depends(get_session)):
    try:
        recompute_players_points_tx(session)
        session.commit()
        return {"status": "ok"}
    except Exception as e:
        session.rollback()
        print(f"ERROR recompute (manual): {repr(e)}")
        raise HTTPException(status_code=500, detail="Recompute failed")
