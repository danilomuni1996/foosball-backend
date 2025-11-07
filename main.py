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

def get_session():
    with Session(engine) as session:
        yield session

# ========== UTILS RICALCOLO PUNTI (CON NOMI COLONNE CORRETTI) ==========
def recompute_players_points_tx(session: Session):
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
    session.exec(text("UPDATE player SET points = 0"))
    session.exec(recompute_query)

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

# ========== MATCHES (CON NOMI COLONNE CORRETTI) ==========
class MatchIn(SQLModel):
    teama_attacker_id: int
    teama_goalkeeper_id: int
    teamb_attacker_id: int
    teamb_goalkeeper_id: int
    score_a: int
    score_b: int

@app.post("/matches", response_model=Match)
def create_match(data: MatchIn, session: Session = Depends(get_session)):
    ids = [
        data.teama_attacker_id, data.teama_goalkeeper_id,
        data.teamb_attacker_id, data.teamb_goalkeeper_id,
    ]
    # ... (il resto del codice usa i nomi corretti presi da `data`)
    m = Match(
        teama_attacker_id=data.teama_attacker_id,
        teama_goalkeeper_id=data.teama_goalkeeper_id,
        teamb_attacker_id=data.teamb_attacker_id,
        teamb_goalkeeper_id=data.teamb_goalkeeper_id,
        # ... resto dei campi
    )
    # ... (logica invariata)
    return m

# ... (il resto del file main.py, come le funzioni per i giocatori, rimane invariato)
# (Assicurati di copiare solo le parti modificate o l'intero file se preferisci)

