from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.auth import create_token, hash_password, verify_password
from app.database import get_session
from app.deps import get_current_profile_id
from app.models import Profile
from app.schemas import LoginRequest, RegisterRequest

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
def register(payload: RegisterRequest, session: Session = Depends(get_session)):
    existing = session.exec(select(Profile).where(Profile.name == payload.name)).first()
    if existing:
        raise HTTPException(409, "That name is already taken")
    if len(payload.password) < 4:
        raise HTTPException(422, "Password must be at least 4 characters")

    profile = Profile(name=payload.name, password_hash=hash_password(payload.password))
    session.add(profile)
    session.commit()
    session.refresh(profile)

    token = create_token(profile.id)
    return {"token": token, "profile": {"id": profile.id, "name": profile.name}}


@router.post("/login")
def login(payload: LoginRequest, session: Session = Depends(get_session)):
    profile = session.exec(select(Profile).where(Profile.name == payload.name)).first()
    # Same error for "no such name" and "wrong password" - don't reveal
    # which one it was, so a client can't use this to enumerate usernames.
    if not profile or not profile.password_hash or not verify_password(payload.password, profile.password_hash):
        raise HTTPException(401, "Invalid name or password")

    token = create_token(profile.id)
    return {"token": token, "profile": {"id": profile.id, "name": profile.name}}


@router.get("/me")
def me(profile_id: str = Depends(get_current_profile_id), session: Session = Depends(get_session)):
    profile = session.get(Profile, profile_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    return {"id": profile.id, "name": profile.name}
