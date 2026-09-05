from pydantic import BaseModel


class WatchlistCreate(BaseModel):
    name: str = "My Watchlist"


class RegisterRequest(BaseModel):
    name: str
    password: str


class LoginRequest(BaseModel):
    name: str
    password: str


class ItemCreate(BaseModel):
    symbol: str
    alert_threshold_pct: float = 2.0
