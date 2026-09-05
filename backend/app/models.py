import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, UniqueConstraint


def gen_uuid() -> str:
    return str(uuid.uuid4())


class Profile(SQLModel, table=True):
    """A real account now - password_hash is set on registration via
    /auth/register. (Earlier iterations of this app had passwordless
    profiles; any such row would have password_hash=None and simply
    couldn't log in until a password is set - there's no migration path
    needed since this was pre-submission development data, not real users.)"""

    id: str = Field(default_factory=gen_uuid, primary_key=True)
    name: str
    password_hash: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Watchlist(SQLModel, table=True):
    """A named collection of symbols, scoped to a Profile via owner_id.
    owner_id is deliberately a plain indexed string, not a hard foreign key
    - this avoids a migration headache on a database that already has rows
    with owner_id="demo-user" from before profiles existed. A production
    version would tighten this to a real FK once the data is migrated."""

    id: str = Field(default_factory=gen_uuid, primary_key=True)
    owner_id: str = Field(default="demo-user", index=True)
    name: str = "My Watchlist"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WatchlistItem(SQLModel, table=True):
    """One symbol inside a watchlist. last_viewed_at is the key field for
    'what changed since I last checked' - we diff against this, not against
    midnight or market open. The unique constraint stops the same symbol
    being added twice to the same watchlist - the API also checks this
    explicitly (see add_item in routers/watchlist.py) since this constraint
    alone won't retroactively apply to a database that already exists."""

    __table_args__ = (UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_symbol"),)

    id: str = Field(default_factory=gen_uuid, primary_key=True)
    watchlist_id: str = Field(foreign_key="watchlist.id", index=True)
    symbol: str = Field(index=True)  # e.g. "RELIANCE.NS", "TCS.NS", "AAPL"
    added_at: datetime = Field(default_factory=datetime.utcnow)
    last_viewed_at: Optional[datetime] = None
    alert_threshold_pct: float = 2.0  # user-configurable "meaningful" cutoff


class PriceSnapshot(SQLModel, table=True):
    """Append-only price history. We NEVER overwrite a row here - each poll
    writes a new snapshot. This is what makes 'what changed since last visit'
    answerable: we can look up the snapshot nearest any past timestamp."""

    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    price: float
    volume: Optional[int] = None
    prev_close: Optional[float] = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    source: str = "yfinance"
    is_stale: bool = False  # true if this snapshot reuses a previous good price
    # after a failed fetch, rather than being a fresh read


class ChangeEvent(SQLModel, table=True):
    """A precomputed record of a 'meaningful' move. Precomputing this at
    poll-time (instead of recalculating on every page load) is what lets the
    read path stay cheap as watchlists and users scale up."""

    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    computed_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    pct_change: float
    score: float  # magnitude used for ranking "what deserves attention"
    reason: str
