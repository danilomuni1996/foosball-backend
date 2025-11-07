import os
import time
from typing import Callable
from sqlmodel import SQLModel, create_engine
from sqlalchemy.exc import OperationalError

# Carica la stringa di connessione dalla variabile d'ambiente.
DATABASE_URL = os.getenv("DATABASE_URL")

# Configura l'engine in modo resiliente per Neon/Postgres (produzione)
# e con un fallback a SQLite per lo sviluppo locale.
if not DATABASE_URL:
    # --- SVILUPPO LOCALE ---
    # Se la variabile d'ambiente DATABASE_URL non è impostata,
    # il codice userà un semplice file di database SQLite chiamato "app.db".
    DATABASE_URL = "sqlite:///app.db"
    engine = create_engine(
        DATABASE_URL,
        # Argomento specifico per SQLite per permettere l'uso da più thread in FastAPI.
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
else:
    # --- PRODUZIONE (NEON POSTGRES) ---
    # Usa la configurazione per PostgreSQL con parametri di pooling robusti,
    # adatti per un ambiente serverless come Neon.
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,       # Controlla la validità della connessione prima di ogni utilizzo.
        pool_size=5,              # Numero di connessioni da tenere pronte nel pool.
        max_overflow=5,           # Numero di connessioni extra che possono essere aperte sotto carico.
        pool_recycle=1800,        # Ricicla le connessioni ogni 30 minuti per prevenire timeout.
    )

def with_retry(fn: Callable, retries: int = 5, delay: float = 2.0):
    """
    Esegue una funzione con tentativi multipli in caso di errori operativi di connessione.
    È utile per database "serverless" come Neon, che potrebbero richiedere un "risveglio".
    """
    for i in range(retries):
        try:
            return fn()
        except OperationalError:
            if i == retries - 1:
                # Se è l'ultimo tentativo, solleva l'eccezione e fallisce.
                raise
            # Attendi con un ritardo che aumenta a ogni tentativo.
            time.sleep(delay * (i + 1))

def init_db(lazy: bool = True):
    """
    Crea le tabelle del database (definite in models.py) se non esistono già.
    
    - lazy=True (default): Non esegue nulla all'avvio. La creazione deve essere
      chiamata manualmente, per esempio tramite un endpoint admin. Questo previene
      fallimenti all'avvio se il database non è immediatamente disponibile.
      
    - lazy=False: Prova a creare le tabelle all'avvio, usando la logica "with_retry"
      per gestire i tempi di risveglio del database.
    """
    def _create():
        # Istruzione standard di SQLModel/SQLAlchemy per creare tutte le tabelle.
        SQLModel.metadata.create_all(engine)

    if lazy:
        return
    else:
        with_retry(_create)
