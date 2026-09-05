from fastapi import Header, HTTPException

from app.auth import decode_token


def get_current_profile_id(authorization: str = Header(None)) -> str:
    """Every protected endpoint depends on this instead of trusting a
    client-supplied owner_id - identity now comes from a verified token,
    not from whatever the request claims. Missing/invalid/expired token all
    collapse to the same 401 rather than leaking which specific thing was
    wrong (avoids giving an attacker a token-guessing oracle)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ")
    profile_id = decode_token(token)
    if not profile_id:
        raise HTTPException(401, "Invalid or expired token")

    return profile_id
