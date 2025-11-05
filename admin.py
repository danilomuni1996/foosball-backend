# admin.py
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
import os

from db import engine
from models import Player, Match  # adatta ai tuoi nomi reali

router = APIRouter(prefix="/admin", tags=["admin"])

def get_session():
    with Session(engine) as session:
        yield session

@router.post("/reset")
def reset_db(session: Session = Depends(get_session)):
    # 1) elimina tutte le partite
    session.exec(select(Match))  # warmup
    session.query(Match).delete()  # per SQLModel/SQLAlchemy 1.4
    session.commit()
    # 2) rimuovi eventuali file foto
    for p in session.exec(select(Player)).all():
        photo_path = getattr(p, "photo_path", None)
        if photo_path and os.path.exists(photo_path):
            try: os.remove(photo_path)
            except Exception: pass
    # 3) elimina tutti i giocatori
    session.query(Player).delete()
    session.commit()
    return {"players": 0, "matches": 0}

@router.delete("/players/{player_id}")
def delete_player(player_id: int, session: Session = Depends(get_session)):
    p = session.get(Player, player_id)
    if not p:
        raise HTTPException(404, "Player not found")
    photo_path = getattr(p, "photo_path", None)
    if photo_path and os.path.exists(photo_path):
        try: os.remove(photo_path)
        except Exception: pass
    session.delete(p)
    session.commit()
    return {"status": "ok"}

@router.delete("/matches/{match_id}")
def delete_match(match_id: int, session: Session = Depends(get_session)):
    m = session.get(Match, match_id)
    if not m:
        raise HTTPException(404, "Match not found")
    session.delete(m)
    session.commit()
    return {"status": "ok"}
