import json
import os
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app.cache import enrichment_key, quote_key, redis_client
from app.database import engine
from app.market_data import fetch_batch_daily, fetch_batch_intraday, fetch_target_price
from app.models import ChangeEvent, PriceSnapshot, WatchlistItem

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
CHANGE_THRESHOLD_PCT = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.5"))
VOLUME_SPIKE_MULTIPLIER = float(os.getenv("VOLUME_SPIKE_MULTIPLIER", "1.5"))
CACHE_TTL_SECONDS = 300
ENRICHMENT_TTL_SECONDS = 900  # refreshed every poll anyway now that it's batched - just needs to outlive one cycle
TARGET_TTL_HOURS = int(os.getenv("TARGET_TTL_HOURS", "24"))
TARGET_TTL_SECONDS = TARGET_TTL_HOURS * 3600
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "0.5"))


def _target_key(symbol: str) -> str:
    return f"target:{symbol}"


def _cache_quote(
    symbol, price, volume, fetched_at, is_stale, is_live=True,
    volume_ratio=None, is_volume_spike=False,
    prev_close=None, pct_change_vs_prev_close=None, is_significant_vs_prev_close=False,
):
    redis_client.set(
        quote_key(symbol),
        json.dumps(
            {
                "price": price,
                "volume": volume,
                "fetched_at": fetched_at.isoformat(),
                "is_stale": is_stale,
                "is_live": is_live,
                "volume_ratio": volume_ratio,
                "is_volume_spike": is_volume_spike,
                "prev_close": prev_close,
                "pct_change_vs_prev_close": pct_change_vs_prev_close,
                "is_significant_vs_prev_close": is_significant_vs_prev_close,
            }
        ),
        ex=CACHE_TTL_SECONDS,
    )


def _handle_fetch_failure(session: Session, symbol: str):
    """Both batch fetches came back with nothing for this symbol - fall back
    to the last known good price and mark it stale, rather than the item
    going blank or showing silently outdated data as current."""
    last_good = session.exec(
        select(PriceSnapshot)
        .where(PriceSnapshot.symbol == symbol)
        .order_by(PriceSnapshot.fetched_at.desc())
    ).first()
    if last_good:
        _cache_quote(symbol, last_good.price, last_good.volume, last_good.fetched_at, is_stale=True)


def _refresh_targets(symbols: list[str]) -> dict:
    """Analyst targets have no batch endpoint, so this is the one remaining
    per-symbol loop - kept deliberately rare (long TTL, default 24h) and
    paced with a small delay between calls, since unpaced per-symbol .info
    calls were exactly what caused the rate-limit trouble earlier."""
    targets = {}
    for symbol in symbols:
        cached = redis_client.get(_target_key(symbol))
        if cached is not None:
            targets[symbol] = json.loads(cached)
            continue

        target = fetch_target_price(symbol)
        redis_client.set(_target_key(symbol), json.dumps(target), ex=TARGET_TTL_SECONDS)
        targets[symbol] = target
        time.sleep(REQUEST_DELAY_SECONDS)

    return targets


def poll_prices():
    with Session(engine) as session:
        symbols = session.exec(select(WatchlistItem.symbol).distinct()).all()
        if not symbols:
            return

        # Two requests total for the whole watchlist, regardless of how many
        # symbols are tracked - not two-per-symbol. This is the core fix for
        # the O(N) request problem that was tripping Yahoo's rate limit.
        daily = fetch_batch_daily(symbols)
        intraday = fetch_batch_intraday(symbols)
        targets = _refresh_targets(symbols)

        for symbol in symbols:
            day_data = daily.get(symbol)
            live = intraday.get(symbol)

            price = (live or {}).get("price") or (day_data or {}).get("price")
            if price is None:
                _handle_fetch_failure(session, symbol)
                continue

            volume = (live or {}).get("volume") or (day_data or {}).get("volume")
            prev_close = (day_data or {}).get("prev_close")
            is_live = live is not None  # drives the freshness badge on the frontend

            snapshot = PriceSnapshot(
                symbol=symbol, price=price, volume=volume, prev_close=prev_close, is_stale=False,
            )
            session.add(snapshot)
            session.commit()
            session.refresh(snapshot)

            volume_ratio = None
            is_volume_spike = False
            avg_vol = (day_data or {}).get("avg_volume")
            if volume and avg_vol:
                volume_ratio = volume / avg_vol
                is_volume_spike = volume_ratio >= VOLUME_SPIKE_MULTIPLIER

            pct_change_vs_prev_close = None
            is_significant_vs_prev_close = False
            if prev_close:
                pct_change_vs_prev_close = (price - prev_close) / prev_close * 100
                is_significant_vs_prev_close = abs(pct_change_vs_prev_close) >= CHANGE_THRESHOLD_PCT

            _cache_quote(
                symbol, snapshot.price, snapshot.volume, snapshot.fetched_at,
                is_stale=False, is_live=is_live,
                volume_ratio=volume_ratio, is_volume_spike=is_volume_spike,
                prev_close=prev_close,
                pct_change_vs_prev_close=pct_change_vs_prev_close,
                is_significant_vs_prev_close=is_significant_vs_prev_close,
            )

            redis_client.set(
                enrichment_key(symbol),
                json.dumps(
                    {
                        "day_open": (day_data or {}).get("day_open"),
                        "day_high": (day_data or {}).get("day_high"),
                        "day_low": (day_data or {}).get("day_low"),
                        "year_high": (day_data or {}).get("year_high"),
                        "year_low": (day_data or {}).get("year_low"),
                        "avg_volume": avg_vol,
                        "target_mean_price": targets.get(symbol),
                    }
                ),
                ex=ENRICHMENT_TTL_SECONDS,
            )

            if is_volume_spike:
                session.add(
                    ChangeEvent(
                        symbol=symbol,
                        pct_change=0,
                        score=volume_ratio,
                        reason=f"Volume {volume_ratio:.1f}x the average",
                    )
                )
                session.commit()

            if is_significant_vs_prev_close:
                session.add(
                    ChangeEvent(
                        symbol=symbol,
                        pct_change=pct_change_vs_prev_close,
                        score=abs(pct_change_vs_prev_close),
                        reason=f"{pct_change_vs_prev_close:+.2f}% vs previous close",
                    )
                )
                session.commit()


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        poll_prices,
        "interval",
        seconds=POLL_INTERVAL_SECONDS,
        id="poll_prices",
        replace_existing=True,
        next_run_time=datetime.utcnow(),
    )
    scheduler.start()
    return scheduler
