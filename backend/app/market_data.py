from typing import Optional

import pandas as pd
import yfinance as yf


def fetch_batch_daily(symbols: list[str]) -> dict:
    """One HTTP request for ALL symbols instead of one call per symbol -
    this is the actual fix for the rate-limit trouble we hit earlier.
    Covers current price, previous close, today's open/high/low, volume,
    a 10-day average volume, and the 52-week high/low - all derived from
    a single year of daily bars per symbol, fetched in one batch request
    via yf.download rather than looping yf.Ticker() per symbol."""
    if not symbols:
        return {}

    try:
        data = yf.download(
            tickers=" ".join(symbols),
            period="1y",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=False,  # avoid aggressive concurrency that can trigger bans
            progress=False,
        )
    except Exception:
        return {}

    results = {}
    for symbol in symbols:
        try:
            df = data[symbol].dropna() if len(symbols) > 1 else data.dropna()
            if df.empty:
                continue

            latest = df.iloc[-1]
            prev_close = float(df.iloc[-2]["Close"]) if len(df) > 1 else float(latest["Open"])
            # ~63 trading days = roughly 3 months, matching the style of
            # Yahoo's own displayed "Avg. Volume" field - so this number is
            # directly comparable to what anyone can look up on Yahoo's own
            # page, rather than an internal figure nobody can cross-check.
            avg_volume = df["Volume"].tail(63).mean()

            results[symbol] = {
                "price": float(latest["Close"]),
                "prev_close": prev_close,
                "day_open": float(latest["Open"]),
                "day_high": float(latest["High"]),
                "day_low": float(latest["Low"]),
                "volume": int(latest["Volume"]) if pd.notna(latest["Volume"]) else None,
                "avg_volume": float(avg_volume) if pd.notna(avg_volume) else None,
                "year_high": float(df["High"].max()),
                "year_low": float(df["Low"].min()),
            }
        except Exception:
            continue

    return results


def fetch_batch_intraday(symbols: list[str]) -> dict:
    """A fresher live price during market hours, again one request for
    every symbol rather than one per symbol. Not every symbol has 1-minute
    data (market closed, thin data for some tickers) - callers should treat
    a symbol missing here as 'fall back to the daily price', not an error."""
    if not symbols:
        return {}

    try:
        data = yf.download(
            tickers=" ".join(symbols),
            period="1d",
            interval="1m",
            group_by="ticker",
            auto_adjust=True,
            threads=False,
            progress=False,
        )
    except Exception:
        return {}

    results = {}
    for symbol in symbols:
        try:
            df = data[symbol].dropna() if len(symbols) > 1 else data.dropna()
            if df.empty:
                continue
            latest = df.iloc[-1]
            results[symbol] = {
                "price": float(latest["Close"]),
                "volume": int(latest["Volume"]) if pd.notna(latest["Volume"]) else None,
            }
        except Exception:
            continue

    return results


def fetch_target_price(symbol: str) -> Optional[float]:
    """Analyst consensus 1-year price target. yfinance has no batch endpoint
    for this field - it only lives in the heavier per-symbol .info payload -
    so this stays a per-symbol call, which is exactly why the scheduler only
    fetches it rarely (long TTL) with a small delay between symbols, instead
    of every poll cycle like the batch price/volume data above."""
    try:
        target = yf.Ticker(symbol).info.get("targetMeanPrice")
        return float(target) if target else None
    except Exception:
        return None
