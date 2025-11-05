from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select, SQLModel
from typing import Optional, List, Dict
from pathlib import Path
from models import Player, Match
from db import init_db, engine
import json

app = FastAPI()

# CORS aperto per test
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# Static per immagini
Path("uploads").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="uploads"), name="static")

@app.on_event("startup")
def on_startup():
    init_db()

def get_session():
    with Session(engine) as session:
        yield session

@app.get("/")
def root():
    return {"ok": True}

@app.post("/players", response_model=Player)
async def create_player(
    name: str = Form(...),
    preferred_role: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session)
):
    photo_url = None
    if photo:
        dest = Path("uploads") / photo.filename
        with dest.open("wb") as f:
            f.write(await photo.read())
        photo_url = f"/static/{photo.filename}"
    p = Player(name=name, preferred_role=preferred_role, photo_url=photo_url)
    session.add(p); session.commit(); session.refresh(p)
    return p

@app.get("/players", response_model=List[Player])
def list_players(session: Session = Depends(get_session)):
    return session.exec(select(Player)).all()

@app.get("/leaderboard", response_model=List[Player])
def leaderboard(session: Session = Depends(get_session)):
    return session.exec(select(Player).order_by(Player.points.desc())).all()

class MatchIn(SQLModel):
    teamA_attacker_id: int
    teamA_goalkeeper_id: int
    teamB_attacker_id: int
    teamB_goalkeeper_id: int
    score_a: int
    score_b: int

@app.post("/matches", response_model=Match)
def create_match(data: MatchIn, session: Session = Depends(get_session)):
    ids = [
        data.teamA_attacker_id, data.teamA_goalkeeper_id,
        data.teamB_attacker_id, data.teamB_goalkeeper_id
    ]
    players = {p.id: p for p in session.exec(select(Player).where(Player.id.in_(ids))).all()}
    if len(players) != 4:
        raise HTTPException(400, "Giocatori non validi")

    winner = "A" if data.score_a > data.score_b else "B"
    teamA = [players[data.teamA_attacker_id], players[data.teamA_goalkeeper_id]]
    teamB = [players[data.teamB_attacker_id], players[data.teamB_goalkeeper_id]]

    avgA = sum(p.points for p in teamA) / 2
    avgB = sum(p.points for p in teamB) / 2

    base_win, base_lose = 3, 1
    upset_gap = 5
    bonus = 1

    awarded: Dict[int, int] = {}
    if winner == "A":
        win_team, lose_team = teamA, teamB
        underdog_win = avgA + upset_gap <= avgB
    else:
        win_team, lose_team = teamB, teamA
        underdog_win = avgB + upset_gap <= avgA

    for p in win_team:
        awarded[p.id] = base_win + (bonus if underdog_win else 0)
    for p in lose_team:
        awarded[p.id] = base_lose

    for pid, pts in awarded.items():
        players[pid].points += pts
    session.add_all(players.values())

    m = Match(
        teamA_attacker_id=data.teamA_attacker_id,
        teamA_goalkeeper_id=data.teamA_goalkeeper_id,
        teamB_attacker_id=data.teamB_attacker_id,
        teamB_goalkeeper_id=data.teamB_goalkeeper_id,
        score_a=data.score_a, score_b=data.score_b,
        winner_team=winner,
        points_awarded=json.dumps(awarded),
    )
    session.add(m); session.commit(); session.refresh(m)
    return m
