import os
import time
from typing import Callable
from sqlmodel import SQLModel, create_engine
from sqlalchemy.exc import OperationalError

DATABASE_URL = os.getenv("DATABASE_URL")

# Configura engine in modo resiliente per Neon/Postgres e fallback SQLite in locale
if not DATABASE_URL:
    # Sviluppo locale: SQLite
    DATABASE_URL = "sqlite:///app.db"
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
else:
    # Produzione: Neon Postgres
    # Assicurati che la URL includa sslmode=require o forzalo via connect_args
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,  # 30 minuti
        connect_args={"sslmode": "require"},
    )

def with_retry(fn: Callable, retries: int = 5, delay: float = 2.0):
    """
    Esegue fn con retry esponenziale su errori di connessione (utile quando Neon "risveglia" il compute).
    """
    for i in range(retries):
        try:
            return fn()
        except OperationalError:
            if i == retries - 1:
                raise
            time.sleep(delay * (i + 1))

def init_db(lazy: bool = True):
    """
    Crea le tabelle se mancano.
    Se lazy=True, non fallisce lo startup: invocare manualmente quando serve (es. in un endpoint admin).
    """
    def _create():
        SQLModel.metadata.create_all(engine)

    if lazy:
        # Non forzare una connessione in startup: lascia che venga chiamata da un endpoint quando il DB è pronto.
        return
    else:
        # Se vuoi forzare la creazione, usiamo retry per superare l'idle/permit di Neon.
        with_retry(_create)
