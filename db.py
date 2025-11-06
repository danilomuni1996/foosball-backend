from sqlmodel import SQLModel, create_engine
import os

# Legge la stringa dal pannello Environment di Render (DATABASE_URL)
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Fallback per sviluppo locale: mantiene SQLite
    DATABASE_URL = "sqlite:///app.db"
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
else:
    # Produzione su Neon (Postgres)
    # Esempio: postgresql://user:pass@host/db?sslmode=require
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,  # utile su piani free per connessioni idle
    )

def init_db():
    SQLModel.metadata.create_all(engine)
