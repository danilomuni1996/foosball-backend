from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Field, create_engine, Session, select
from typing import List, Optional
import os
from contextlib import contextmanager
import datetime
import shutil

# --- Configurazione Generale ---
DATABASE_FILE = "foosball.db"
DATABASE_URL = f"sqlite:///{DATABASE_FILE}"
UPLOADS_DIR = "uploads"

# --- Modelli Dati ---
class PlayerBase(SQLModel):
    name: str
    preferred_role: Optional[str] = None

class Player(PlayerBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    photo_url: Optional[str] = None
    
    # Campi calcolati dinamicamente, non memorizzati nel DB
    points: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    matches_played: int = 0

class PlayerCreate(PlayerBase):
    pass

class PlayerReadWithStats(PlayerBase):
    id: int
    photo_url: Optional[str]
    points: int
    wins: int
    losses: int
    draws: int
    matches_played: int

class Match(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    teamA_attacker_id: int
    teamA_goalkeeper_id: int
    teamB_attacker_id: int
    teamB_goalkeeper_id: int
    score_a: int
    score_b: int
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)

class MatchCreate(SQLModel):
    teamA_attacker_id: int
    teamA_goalkeeper_id: int
    teamB_attacker_id: int
    teamB_goalkeeper_id: int
    score_a: int
    score_b: int

# --- Gestione Database ---
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    if not os.path.exists(UPLOADS_DIR):
        os.makedirs(UPLOADS_DIR)

@contextmanager
def get_session():
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()

# --- App FastAPI ---
app = FastAPI()

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.on_event("startup")
def on_startup():
    create_db_and_tables()

# --- Funzioni di Logica ---
def get_player_stats(session: Session) -> List[PlayerReadWithStats]:
    """Calcola le statistiche per ogni giocatore basandosi sulle partite."""
    players = session.exec(select(Player)).all()
    matches = session.exec(select(Match)).all()
    
    player_stats = {p.id: p for p in players}

    for p in player_stats.values():
        p.points = 0
        p.wins = 0
        p.losses = 0
        p.draws = 0
        p.matches_played = 0

    for match in matches:
        team_a_ids = [match.teamA_attacker_id, match.teamA_goalkeeper_id]
        team_b_ids = [match.teamB_attacker_id, match.teamB_goalkeeper_id]
        
        all_match_players = team_a_ids + team_b_ids
        for player_id in all_match_players:
            if player_id in player_stats:
                player_stats[player_id].matches_played += 1

        if match.score_a > match.score_b:
            for player_id in team_a_ids:
                if player_id in player_stats:
                    player_stats[player_id].points += 3
                    player_stats[player_id].wins += 1
            for player_id in team_b_ids:
                if player_id in player_stats:
                    player_stats[player_id].losses += 1
        elif match.score_b > match.score_a:
            for player_id in team_b_ids:
                if player_id in player_stats:
                    player_stats[player_id].points += 3
                    player_stats[player_id].wins += 1
            for player_id in team_a_ids:
                if player_id in player_stats:
                    player_stats[player_id].losses += 1
        else:
            for player_id in all_match_players:
                 if player_id in player_stats:
                    player_stats[player_id].points += 1
                    player_stats[player_id].draws += 1

    return list(player_stats.values())


# --- Endpoints ---
@app.get("/leaderboard", response_model=List[PlayerReadWithStats])
def get_leaderboard(session: Session = Depends(get_session)):
    """Ritorna la classifica dei giocatori con statistiche complete."""
    player_stats = get_player_stats(session)
    # Ordina i giocatori per punti in ordine decrescente
    sorted_players = sorted(player_stats, key=lambda p: p.points, reverse=True)
    return sorted_players

@app.get("/players", response_model=List[PlayerReadWithStats])
def read_players(session: Session = Depends(get_session)):
    """Ritorna tutti i giocatori con le loro statistiche aggiornate."""
    player_stats = get_player_stats(session)
    return sorted(player_stats, key=lambda p: p.name)

@app.post("/players", response_model=PlayerReadWithStats, status_code=201)
def create_player(name: str, preferred_role: Optional[str] = None, photo: UploadFile = File(None), session: Session = Depends(get_session)):
    photo_url = None
    if photo:
        file_path = os.path.join(UPLOADS_DIR, photo.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(photo.file, buffer)
        photo_url = f"/uploads/{photo.filename}"

    player = Player(name=name, preferred_role=preferred_role, photo_url=photo_url)
    session.add(player)
    session.commit()
    session.refresh(player)
    # Ritorna il giocatore con le statistiche iniziali
    return PlayerReadWithStats(**player.model_dump(), points=0, wins=0, losses=0, draws=0, matches_played=0)


@app.delete("/players/{player_id}", status_code=204)
def delete_player(player_id: int, session: Session = Depends(get_session)):
    player = session.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    session.delete(player)
    session.commit()
    return

@app.get("/matches", response_model=List[Match])
def read_matches(session: Session = Depends(get.session)):
    matches = session.exec(select(Match)).all()
    return matches

@app.post("/matches", response_model=Match, status_code=201)
def create_match(match: MatchCreate, session: Session = Depends(get_session)):
    db_match = Match.model_validate(match)
    session.add(db_match)
    session.commit()
    session.refresh(db_match)
    return db_match

@app.delete("/matches/{match_id}", status_code=204)
def delete_match(match_id: int, session: Session = Depends(get_session)):
    match = session.get(Match, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    session.delete(match)
    session.commit()
    return
