import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.cache import enrichment_key, quote_key, redis_client
from app.database import get_session
from app.deps import get_current_profile_id
from app.models import PriceSnapshot, Watchlist, WatchlistItem
from app.schemas import ItemCreate, WatchlistCreate
from app.scoring import compute_attention_score

router = APIRouter(prefix="/watchlists", tags=["watchlists"])

ATTENTION_SCORE_THRESHOLD = int(os.getenv("ATTENTION_SCORE_THRESHOLD", "40"))
VOLUME_SPIKE_MULTIPLIER = float(os.getenv("VOLUME_SPIKE_MULTIPLIER", "1.5"))


def _get_owned_watchlist(watchlist_id: str, profile_id: str, session: Session) -> Watchlist:
    wl = session.get(Watchlist, watchlist_id)
    if not wl:
        raise HTTPException(404, "Watchlist not found")
    if wl.owner_id != profile_id:
        raise HTTPException(403, "This watchlist doesn't belong to you")
    return wl


@router.post("/")
def create_watchlist(
    payload: WatchlistCreate,
    profile_id: str = Depends(get_current_profile_id),
    session: Session = Depends(get_session),
):
    wl = Watchlist(name=payload.name, owner_id=profile_id)
    session.add(wl)
    session.commit()
    session.refresh(wl)
    return wl


@router.get("/")
def list_watchlists(
    profile_id: str = Depends(get_current_profile_id),
    session: Session = Depends(get_session),
):
    return session.exec(select(Watchlist).where(Watchlist.owner_id == profile_id)).all()


@router.delete("/{watchlist_id}")
def delete_watchlist(
    watchlist_id: str,
    profile_id: str = Depends(get_current_profile_id),
    session: Session = Depends(get_session),
):
    wl = _get_owned_watchlist(watchlist_id, profile_id, session)

    # flush() sends the DELETE statements to Postgres in the order we choose,
    # but still inside the SAME transaction as the parent delete below - one
    # commit() at the end means both succeed or both roll back together.
    # An earlier version of this used two separate commit() calls, which
    # respected the child-before-parent ordering Postgres requires but was
    # NOT actually atomic: a crash between the two commits could leave items
    # deleted with the parent watchlist still orphaned. flush() gets the
    # ordering right without giving up atomicity.
    items = session.exec(
        select(WatchlistItem).where(WatchlistItem.watchlist_id == watchlist_id)
    ).all()
    for item in items:
        session.delete(item)
    session.flush()

    session.delete(wl)
    session.commit()
    return {"ok": True}


@router.post("/{watchlist_id}/items")
def add_item(
    watchlist_id: str,
    payload: ItemCreate,
    profile_id: str = Depends(get_current_profile_id),
    session: Session = Depends(get_session),
):
    _get_owned_watchlist(watchlist_id, profile_id, session)

    symbol = payload.symbol.upper().strip()
    existing = session.exec(
        select(WatchlistItem)
        .where(WatchlistItem.watchlist_id == watchlist_id)
        .where(WatchlistItem.symbol == symbol)
    ).first()
    if existing:
        raise HTTPException(409, f"{symbol} is already in this watchlist")

    item = WatchlistItem(
        watchlist_id=watchlist_id,
        symbol=symbol,
        alert_threshold_pct=payload.alert_threshold_pct,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.delete("/{watchlist_id}/items/{item_id}")
def remove_item(
    watchlist_id: str,
    item_id: str,
    profile_id: str = Depends(get_current_profile_id),
    session: Session = Depends(get_session),
):
    _get_owned_watchlist(watchlist_id, profile_id, session)

    item = session.get(WatchlistItem, item_id)
    if not item or item.watchlist_id != watchlist_id:
        raise HTTPException(404, "Item not found")
    session.delete(item)
    session.commit()
    return {"ok": True}


@router.get("/{watchlist_id}/view")
def view_watchlist(
    watchlist_id: str,
    profile_id: str = Depends(get_current_profile_id),
    session: Session = Depends(get_session),
):
    """For each symbol: latest price (Redis hot path), how much it's moved
    since THIS user last viewed it, an attention score combining price +
    volume + 52-week proximity signals, and a freshness classification
    (live / delayed / stale) based on what data was actually available at
    the last poll - not a guess, a reflection of what the scheduler saw."""
    _get_owned_watchlist(watchlist_id, profile_id, session)

    items = session.exec(
        select(WatchlistItem).where(WatchlistItem.watchlist_id == watchlist_id)
    ).all()

    result = []
    now = datetime.utcnow()

    for item in items:
        cached = redis_client.get(quote_key(item.symbol))
        quote = json.loads(cached) if cached else None

        enrichment_cached = redis_client.get(enrichment_key(item.symbol))
        enrichment = json.loads(enrichment_cached) if enrichment_cached else {}

        delta_since_last_view_pct = None
        if item.last_viewed_at and quote:
            baseline = session.exec(
                select(PriceSnapshot)
                .where(PriceSnapshot.symbol == item.symbol)
                .where(PriceSnapshot.fetched_at <= item.last_viewed_at)
                .order_by(PriceSnapshot.fetched_at.desc())
            ).first()
            if baseline and baseline.price:
                delta_since_last_view_pct = (
                    (quote["price"] - baseline.price) / baseline.price * 100
                )

        attention_score, attention_triggers = compute_attention_score(
            delta_pct=delta_since_last_view_pct,
            price_threshold_pct=item.alert_threshold_pct,
            volume_ratio=quote.get("volume_ratio") if quote else None,
            volume_threshold=VOLUME_SPIKE_MULTIPLIER,
            price=quote.get("price") if quote else None,
            year_high=enrichment.get("year_high"),
            year_low=enrichment.get("year_low"),
        )
        is_meaningful = attention_score >= ATTENTION_SCORE_THRESHOLD

        if not quote or quote.get("is_stale"):
            freshness = "stale"
        elif not quote.get("is_live", True):
            freshness = "delayed"
        else:
            freshness = "live"

        result.append(
            {
                "item_id": item.id,
                "symbol": item.symbol,
                "price": quote["price"] if quote else None,
                "is_stale": quote["is_stale"] if quote else True,
                "has_data": quote is not None,
                "freshness": freshness,
                "delta_since_last_view_pct": delta_since_last_view_pct,
                "is_meaningful_change": is_meaningful,
                "attention_score": attention_score,
                "attention_triggers": attention_triggers,
                "alert_threshold_pct": item.alert_threshold_pct,
                "day_open": enrichment.get("day_open"),
                "day_high": enrichment.get("day_high"),
                "day_low": enrichment.get("day_low"),
                "year_high": enrichment.get("year_high"),
                "year_low": enrichment.get("year_low"),
                "target_mean_price": enrichment.get("target_mean_price"),
                "volume": quote.get("volume") if quote else None,
                "avg_volume": enrichment.get("avg_volume"),
                "volume_ratio": quote.get("volume_ratio") if quote else None,
                "is_volume_spike": bool(quote.get("is_volume_spike")) if quote else False,
                "prev_close": quote.get("prev_close") if quote else None,
                "pct_change_vs_prev_close": quote.get("pct_change_vs_prev_close") if quote else None,
                "is_significant_vs_prev_close": bool(quote.get("is_significant_vs_prev_close")) if quote else False,
            }
        )

        item.last_viewed_at = now
        session.add(item)

    session.commit()

    # rank by attention score, not raw price delta - a volume-driven or
    # 52-week-proximity signal can now outrank a symbol with a slightly
    # bigger price move but no other corroborating signal
    result.sort(key=lambda r: r["attention_score"], reverse=True)
    return result
