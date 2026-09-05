"""
Manual test-data seeder - NOT part of the live app, NOT run by the
scheduler, NOT imported by anything else. Use this when the market is
closed (or Yahoo is unavailable) to populate realistic-looking data so you
can demo/verify the attention score, volume spike, "vs previous close",
and freshness logic without waiting for real market movement.

This writes directly to Postgres (PriceSnapshot rows, tagged
source="test_seed" so they're identifiable) and to the Redis quote/
enrichment cache - the exact same places the real scheduler writes to.
It does not modify scheduler.py, market_data.py, or any live-fetch code.

IMPORTANT - this data is temporary: the next real scheduler poll (every
POLL_INTERVAL_SECONDS, default 60s) will overwrite the Redis cache with
whatever Yahoo actually returns. If you want a longer testing window,
temporarily raise POLL_INTERVAL_SECONDS in .env (e.g. to 3600) and restart
uvicorn before running this, then set it back before your real demo.

Usage (from backend/, venv active, Postgres + Redis running):
    python seed_demo_data.py

Seeds every symbol currently in any watchlist, cycling through four
scenarios so you can see every badge/flag at least once: a big price move,
a volume spike, a near-52-week-high, and a quiet baseline with nothing
flagged.
"""

import json
import random
from datetime import datetime, timedelta

from dotenv import load_dotenv

# Load before anything else, same reasoning as main.py: modules imported
# below (database.py, cache.py) read environment variables at import time.
load_dotenv()

from sqlmodel import Session, select

from app.cache import enrichment_key, quote_key, redis_client
from app.database import engine, init_db
from app.models import PriceSnapshot, WatchlistItem

POINTS = 60  # roughly an hour of 1-minute snapshots, enough for a real-looking chart
INTERVAL_MINUTES = 1
SCENARIOS = ["big_move", "volume_spike", "near_52w_high", "quiet"]


def seed_symbol(session: Session, symbol: str, scenario: str):
    base_price = random.uniform(100, 2000)
    now = datetime.utcnow()

    prices = [base_price]
    for _ in range(POINTS - 1):
        drift = random.uniform(-0.003, 0.003)
        prices.append(prices[-1] * (1 + drift))

    if scenario == "big_move":
        prices[-1] = prices[0] * 1.04  # +4% across this window - clears the price threshold
    elif scenario == "near_52w_high":
        prices[-1] = prices[0] * 1.001  # barely moved, but will sit right at the 52w high
    elif scenario == "quiet":
        prices[-1] = prices[0] * random.uniform(0.998, 1.002)  # pin it small - this scenario should show no flags

    if scenario == "quiet":
        prev_close = prices[0] * random.uniform(0.997, 1.003)  # tight, so it doesn't randomly cross the threshold
    else:
        prev_close = prices[0] * random.uniform(0.98, 1.02)

    volume = 0
    for i, price in enumerate(prices):
        fetched_at = now - timedelta(minutes=(POINTS - 1 - i) * INTERVAL_MINUTES)
        volume = random.randint(500_000, 2_000_000)
        if scenario == "volume_spike" and i == POINTS - 1:
            volume = int(volume * 3)  # comfortably clears VOLUME_SPIKE_MULTIPLIER

        session.add(
            PriceSnapshot(
                symbol=symbol,
                price=price,
                volume=volume,
                prev_close=prev_close,
                fetched_at=fetched_at,
                source="test_seed",
                is_stale=False,
            )
        )
    session.commit()

    final_price = prices[-1]
    final_volume = volume
    if scenario == "volume_spike":
        avg_volume = final_volume / 3.5  # guarantees the ratio clears the spike threshold
    else:
        # keep other scenarios comfortably under the spike threshold so each
        # demo case shows exactly the one flag it's meant to, not a random
        # coincidental overlap
        avg_volume = final_volume * random.uniform(0.8, 1.2)

    volume_ratio = final_volume / avg_volume if avg_volume else None
    is_volume_spike = bool(volume_ratio and volume_ratio >= 1.5)

    pct_change_vs_prev_close = (final_price - prev_close) / prev_close * 100
    is_significant_vs_prev_close = abs(pct_change_vs_prev_close) >= 1.5

    if scenario == "near_52w_high":
        year_high = final_price * 1.001
    else:
        year_high = final_price * random.uniform(1.1, 1.4)
    year_low = final_price * random.uniform(0.6, 0.85)

    redis_client.set(
        quote_key(symbol),
        json.dumps({
            "price": final_price,
            "volume": final_volume,
            "fetched_at": now.isoformat(),
            "is_stale": False,
            "is_live": True,
            "volume_ratio": volume_ratio,
            "is_volume_spike": is_volume_spike,
            "prev_close": prev_close,
            "pct_change_vs_prev_close": pct_change_vs_prev_close,
            "is_significant_vs_prev_close": is_significant_vs_prev_close,
        }),
        ex=3600,
    )
    redis_client.set(
        enrichment_key(symbol),
        json.dumps({
            "day_open": prices[0],
            "day_high": max(prices),
            "day_low": min(prices),
            "year_high": year_high,
            "year_low": year_low,
            "avg_volume": avg_volume,
            "target_mean_price": final_price * random.uniform(1.05, 1.3),
        }),
        ex=3600,
    )
    print(
        f"Seeded {symbol:12s} [{scenario:15s}] price={final_price:8.2f}  "
        f"vol_ratio={volume_ratio:.2f}x  vs_prev_close={pct_change_vs_prev_close:+.2f}%"
    )


def main():
    init_db()

    with Session(engine) as session:
        symbols = session.exec(select(WatchlistItem.symbol).distinct()).all()

        if not symbols:
            print("No symbols found in any watchlist yet - add some via the app first, then re-run this.")
            return

        for i, symbol in enumerate(symbols):
            seed_symbol(session, symbol, SCENARIOS[i % len(SCENARIOS)])

    print(
        "\nDone. Refresh the frontend now.\n"
        "Reminder: the next real scheduler poll will overwrite this with live data "
        "(or a stale/failed result if Yahoo is still unavailable) - if you need a "
        "longer testing window, raise POLL_INTERVAL_SECONDS in .env and restart uvicorn "
        "before re-running this script."
    )


if __name__ == "__main__":
    main()
