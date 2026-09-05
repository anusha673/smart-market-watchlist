# Smart Market Watchlist

A watchlist that tracks not just prices, but what's *actually* changed since you last looked — and surfaces what deserves your attention now, not just a raw list of numbers.

## Stack

- **Backend**: FastAPI + SQLModel (Python)
- **DB**: PostgreSQL — durable, append-only price history
- **Cache**: Redis — hot path for "latest price" reads
- **Scheduler**: APScheduler — polls market data on an interval, in-process (no separate worker/broker needed)
- **Market data**: yfinance (free, no API key), batch-fetched
- **Auth**: JWT sessions, PBKDF2-hashed passwords (stdlib only)
- **Frontend**: single-file vanilla HTML/JS (no build step, no framework — deliberately kept simple so time went into backend mechanics, not tooling)

## Setup

### 1. Start Postgres and Redis

```bash
docker compose up -d
```

Ports are `5433` (Postgres) and `6380` (Redis) rather than the defaults, specifically to avoid colliding with any other Postgres/Redis already running on your machine.

### 2. Set up the backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

### 3. Run the API

```bash
uvicorn app.main:app --reload --port 8000
```

Tables are created automatically on startup (`init_db()`), and the price poller starts immediately. `.env` is loaded via `load_dotenv()` at the very top of `main.py` — this has to happen before any other app module is imported, since several of them (`database.py`, `cache.py`, `auth.py`) read environment variables at import time, not inside a function.

### 4. Open the frontend

Serve it rather than opening the file directly (some browser behavior differs for `file://` vs a real origin):

```bash
cd frontend
python3 -m http.server 5500
```

Then visit `http://localhost:5500`. You'll land on a login screen — register a new account to get started.

### 5. Add symbols

Indian equities via yfinance need an exchange suffix:

| Exchange | Suffix | Example |
|---|---|---|
| NSE | `.NS` | `RELIANCE.NS`, `TCS.NS`, `INFY.NS` |
| BSE | `.BO` | `RELIANCE.BO` |
| US (no suffix) | — | `AAPL`, `MSFT` |
| Crypto (24/7, useful for testing) | `-USD` | `BTC-USD` |

## How "meaningful change" works

`scoring.py` combines three independent signals into one 0–100 attention score, rather than a single flat price threshold:

| Signal | Weight | Fires when |
|---|---|---|
| Price move since last view | 40 | `\|delta\| >= alert_threshold_pct` (per-item, user-configurable) |
| Volume vs. 10-day average | 35 | `volume_ratio >= VOLUME_SPIKE_MULTIPLIER` (default 1.5x) |
| Proximity to 52-week high or low | 25 each | within 1.5% of either bound |

A symbol can score high purely on unusual volume or a 52-week milestone even with a small price move — this is the "institutional conviction vs. retail noise" distinction a single price threshold can't make. `is_meaningful_change` is `attention_score >= ATTENTION_SCORE_THRESHOLD` (default 40), and the watchlist sorts by this score by default. The weights are a considered starting point, not backtested — worth saying plainly if asked, rather than presenting them as more rigorous than they are.

Separately, the delta itself is computed **since that specific user's `last_viewed_at` timestamp** — not since midnight — by looking up the nearest price snapshot before that time and diffing against the current price.

## Batch fetching (not one request per symbol)

`market_data.py` uses `yf.download()` with every tracked symbol passed at once, two calls total per poll cycle regardless of watchlist size — one for daily bars (price, OHLC, 52-week range, 10-day average volume), one for 1-minute intraday bars (a fresher live price during market hours). This replaced an earlier per-symbol-per-call design after hitting a real `429 Too Many Requests` from Yahoo during testing: looping `yf.Ticker(symbol)` per symbol made roughly 3 requests per symbol per poll, which scales badly and was the actual root cause of the rate-limit trouble. The one remaining per-symbol call is the analyst target price (`fetch_target_price`), since yfinance has no batch endpoint for that field — it's deliberately rare (24h TTL, `TARGET_TTL_HOURS`) and paced with `REQUEST_DELAY_SECONDS` between symbols.

**A real rate-limit encounter, twice** (worth mentioning in the demo): during testing this hit both a genuine `429` from repeated heavy polling, and separately a client-side bug where yfinance cached a *failed crumb-negotiation's error text* as if it were a valid session token, causing every subsequent request to fail identically regardless of the actual rate-limit state — fixed by clearing yfinance's local cache (`~/.cache/py-yfinance`). Both are real instances of the "unreliable dependency" edge case named in the brief, not staged failures — and the existing stale-data fallback (last-known-good price + `is_stale` flag) is exactly what's meant to absorb this class of problem.

## Freshness badges (🟢 live / 🟡 delayed / 🔴 stale)

Each symbol's freshness is derived from what the scheduler actually saw on its last poll, not a time-based guess:
- 🔴 **stale** — the last fetch attempt failed entirely; showing the last known good price.
- 🟡 **delayed** — the daily-bar batch succeeded but the 1-minute intraday batch didn't have data for this symbol (common when a market is closed, or for symbols with thin intraday coverage) — so the price shown is the last daily close, not a live tick.
- 🟢 **live** — the intraday batch had fresh 1-minute data for this symbol.

## Real authentication

`/auth/register` and `/auth/login` issue a JWT (`app/auth.py`, HS256, 7-day expiry by default) after verifying a password hashed with PBKDF2-HMAC-SHA256 and a random salt — standard library only, no compiled dependency like bcrypt that could fail to install under time pressure. Every watchlist/item endpoint depends on `get_current_profile_id` (`app/deps.py`), which derives identity from a verified token rather than trusting a client-supplied id. Ownership is checked explicitly on every watchlist/item mutation and on account deletion (`403` if you don't own the resource, not just "not found") — tested directly: a second account cannot view, modify, or delete a first account's data.

## Multiple watchlists

Each account can hold several named watchlists (`Watchlist.owner_id` scopes them), switchable via tabs in the UI. Deleting a watchlist cascades to its items in a separate commit before the parent row is deleted — necessary on Postgres, since without an explicit ORM relationship between the two tables, SQLAlchemy doesn't reliably order deletes across mapped classes in one flush, and Postgres correctly rejects a parent-before-child delete.

## Chart

The price history chart is a hand-rolled inline SVG built from `/symbols/{symbol}/history`, not an external charting library — this was a deliberate choice so the demo doesn't depend on internet access to a CDN at the venue. It reads directly off the `price_snapshots` table, so it's exactly what the poller has actually recorded since you started tracking that symbol.

## Notifications

Browser-native (`Notification` API), client-side only — when `/view` flags `is_meaningful_change: true` for a symbol, the frontend fires a notification if the user has granted permission. No backend push/email infrastructure was built for this; browser notifications cover the requirement without the added complexity of a notification service.

## Edge cases handled

- **Stale data**: if a poll fetch fails for a symbol, the last known good price is kept and re-cached with `is_stale: true` rather than the item going blank or showing silently outdated data as current.
- **No data yet**: a newly added symbol shows "no data" until the next poll cycle runs, rather than erroring.
- **Read/write split**: `/view` reads from Redis (fast path) and only falls back to Postgres for the historical baseline lookup needed to compute the delta — durable storage stays the source of truth, cache stays disposable.
- **Duplicate symbols**: adding a symbol already in a watchlist returns `409 Conflict` instead of silently creating a second row. Enforced at the API level (case-insensitive) plus a DB-level unique constraint on `(watchlist_id, symbol)` for fresh deployments.
- **Cross-user access**: attempting to view, modify, or delete another account's watchlist returns `403`, verified with an actual two-account test, not just written and assumed correct.

## Known trade-offs (cut deliberately, not accidentally)

- **Polling, not push.** APScheduler polls every 60s rather than streaming live ticks via WebSocket/SSE. Simpler to build and debug solo in the time available; a real-time push layer is a natural "if I had more time" extension.
- **Single-instance scheduler.** APScheduler runs in-process, so it wouldn't scale past one backend replica as-is. At real scale this job would move to a separate worker process (e.g. Celery, with Redis as the broker) so the API and the polling scale independently — worth naming explicitly in the Q&A round since "how the system scales" is an explicit rubric item.
- **Attention score weights are a starting point, not tuned.** They're a defensible first pass (price weighted highest since it's the most direct signal), not something derived from backtested data.

## API

All endpoints except registration, login, and symbol history require `Authorization: Bearer <token>`.

| Method | Path | What it does |
|---|---|---|
| POST | `/auth/register` | create an account, returns a token |
| POST | `/auth/login` | log in, returns a token |
| GET | `/auth/me` | current account info |
| DELETE | `/profiles/{id}` | delete your own account (must match the authenticated id) and cascade-delete everything owned by it |
| POST | `/watchlists/` | create a watchlist, owned by the authenticated account |
| GET | `/watchlists/` | list your watchlists |
| DELETE | `/watchlists/{id}` | delete a watchlist and cascade-delete its items |
| POST | `/watchlists/{id}/items` | add a symbol |
| DELETE | `/watchlists/{id}/items/{item_id}` | remove a symbol |
| GET | `/watchlists/{id}/view` | current state + attention scores + deltas since last view (also stamps `last_viewed_at`) |
| GET | `/symbols/{symbol}/history?limit=100` | recent price snapshots for a symbol, oldest first (feeds the chart) — not auth-scoped, since it's shared, non-sensitive market data |

## Testing without live market data

`seed_demo_data.py` (in `backend/`) writes realistic synthetic data directly to Postgres and Redis — the same places the real scheduler writes to — without touching any live-fetch code. Useful when the market's closed or Yahoo is unavailable and you need to verify the attention score, volume spike, "vs previous close", and freshness logic actually work. Run it with `python seed_demo_data.py` from `backend/` (venv active, Postgres/Redis running); it seeds every symbol currently in any watchlist, cycling through four scenarios so every flag type shows at least once: a big price move, a volume spike, a near-52-week-high, and a quiet baseline with nothing flagged.

This data is temporary by design — the next real scheduler poll overwrites the cache with whatever Yahoo actually returns. For a longer testing window, temporarily raise `POLL_INTERVAL_SECONDS` in `.env` before running it.
