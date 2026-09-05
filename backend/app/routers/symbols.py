from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.database import get_session
from app.models import PriceSnapshot

router = APIRouter(prefix="/symbols", tags=["symbols"])


@router.get("/{symbol}/history")
def symbol_history(symbol: str, limit: int = 100, session: Session = Depends(get_session)):
    """Recent price snapshots for a symbol, oldest first - feeds the sparkline
    chart on the frontend. Pulled straight from the append-only snapshot
    table, so this is exactly what the poller has actually recorded, not a
    live re-fetch."""
    snapshots = session.exec(
        select(PriceSnapshot)
        .where(PriceSnapshot.symbol == symbol.upper())
        .order_by(PriceSnapshot.fetched_at.desc())
        .limit(limit)
    ).all()
    snapshots.reverse()
    return [
        {"price": s.price, "fetched_at": s.fetched_at.isoformat()} for s in snapshots
    ]
