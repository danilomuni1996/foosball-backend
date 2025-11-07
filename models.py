from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime

class Player(SQLModel, table=True):
    # Specifica il nome esatto della tabella: 'player'
    __tablename__ = "player"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    photo_url: Optional[str] = None
    preferred_role: Optional[str] = None
    points: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Match(SQLModel, table=True):
    # Specifica il nome esatto della tabella: 'match'
    __tablename__ = "match"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    teamA_attacker_id: int
    teamA_goalkeeper_id: int
    teamB_attacker_id: int
    teamB_goalkeeper_id: int
    score_a: int
    score_b: int
    winner_team: str
    points_awarded: str

