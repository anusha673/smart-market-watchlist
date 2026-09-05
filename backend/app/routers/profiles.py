from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.deps import get_current_profile_id
from app.models import Profile, Watchlist, WatchlistItem

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.delete("/{profile_id}")
def delete_profile(
    profile_id: str,
    current_profile_id: str = Depends(get_current_profile_id),
    session: Session = Depends(get_session),
):
    # Ownership check: a token only lets you delete YOUR OWN account, never
    # someone else's by guessing/passing a different id in the URL.
    if profile_id != current_profile_id:
        raise HTTPException(403, "You can only delete your own account")

    profile = session.get(Profile, profile_id)
    if not profile:
        raise HTTPException(404, "Profile not found")

    watchlists = session.exec(
        select(Watchlist).where(Watchlist.owner_id == profile_id)
    ).all()

    # Same fix as delete_watchlist: flush() between tiers gets the delete
    # order right (items, then watchlists, then the profile itself) while
    # keeping everything in ONE transaction - a single commit() at the end
    # means the whole cascade succeeds or rolls back together, not partially.
    for wl in watchlists:
        items = session.exec(
            select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id)
        ).all()
        for item in items:
            session.delete(item)
    session.flush()

    for wl in watchlists:
        session.delete(wl)
    session.flush()

    session.delete(profile)
    session.commit()
    return {"ok": True}
