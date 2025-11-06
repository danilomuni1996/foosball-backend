# admin.py
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, SQLModel
from sqlalchemy import text
import os

from db import engine
from models import Player, Match  # adatta ai tuoi nomi reali

router = APIRouter(prefix="/admin", tags=["admin"])

def get_session():
    with Session(engine) as session:
        yield session

@router.post("/init-db")
def init_db_endpoint():
    """
    Crea lo schema (tabelle) se mancano.
    Da chiamare una volta dopo il deploy, dato che lo startup è 'lazy'.
    """
    try:
        SQLModel.metadata.create_all(engine)
        return {"status": "ok", "message": "Schema created/verified"}
    except Exception as e:
        print("ERROR /admin/init-db:", repr(e))
        raise HTTPException(status_code=500, detail="Init DB failed")

@router.post("/reset")
def reset_db(session: Session = Depends(get_session)):
    """
    Svuota le partite e (opzionale) azzera i punti dei giocatori.
    Con calcolo on-demand non è necessario toccare players.points per la UI,
    ma puoi azzerarlo se lo utilizzi altrove.
    """
    try:
        # 1) elimina tutte le partite (stile SQLAlchemy 2.0)
        session.exec(text("DELETE FROM matches"))
        session.commit()

        # 2) opzione: azzera points dei giocatori (se mantieni la colonna e ti serve altrove)
        # session.exec(text("UPDATE players SET points = 0"))
        # session.commit()

        # 3) opzione: rimuovi eventuali file foto se salvi path assoluti (non necessari se usi /static/)
        players = session.exec(select(Player)).all()
        for p in players:
            photo_path = getattr(p, "photo_path", None)
            if photo_path and os.path.exists(photo_path):
                try:
                    os.remove(photo_path)
                except Exception:
                    pass

        return {"players": session.exec(select(Player)).count(), "matches": 0}
    except Exception as e:
        session.rollback()
        print("ERROR /admin/reset:", repr(e))
        raise HTTPException(status_code=500, detail="Reset failed")

@router.delete("/players/{player_id}")
def delete_player(player_id: int, session: Session = Depends(get_session)):
    p = session.get(Player, player_id)
    if not p:
        raise HTTPException(status_code=404, detail="Player not found")

    photo_path = getattr(p, "photo_path", None)
    if photo_path and os.path.exists(photo_path):
        try:
            os.remove(photo_path)
        except Exception:
            pass

    session.delete(p)
    session.commit()
    return {"status": "ok"}

@router.delete("/matches/{match_id}")
def delete_match(match_id: int, session: Session = Depends(get_session)):
    m = session.get(Match, match_id)
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    session.delete(m)
    session.commit()
    return {"status": "ok"}
