import os

from dotenv import load_dotenv
from sqlmodel import SQLModel, create_engine, Session

# Loaded here, not just in main.py - database.py is the first module almost
# everything imports (the app, seed_demo_data.py, any future script), so
# centralizing it here means .env gets loaded regardless of entry point,
# instead of every new script needing to remember to call this itself.
load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/watchlist"
)

# pool_pre_ping avoids using a dead connection after Postgres restarts/idles out
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)


def get_session():
    with Session(engine) as session:
        yield session


def init_db():
    # Import models so SQLModel's metadata knows about every table before create_all.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
