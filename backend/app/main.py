from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Must run before any other app module is imported - several of them (e.g.
# database.py, cache.py) read environment variables at import time, not
# inside a function, so loading .env has to happen first or those reads
# silently fall back to the hardcoded defaults instead.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routers import auth, profiles, symbols, watchlist
from app.scheduler import start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = start_scheduler()
    yield
    scheduler.shutdown()


app = FastAPI(title="Smart Market Watchlist", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(watchlist.router)
app.include_router(symbols.router)
app.include_router(profiles.router)
app.include_router(auth.router)


@app.get("/health")
def health():
    return {"status": "ok"}
