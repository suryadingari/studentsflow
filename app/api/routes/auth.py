"""Login, current-session, and administrator user provisioning endpoints."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.auth.security import (Principal, admin_principal, current_principal,
                               hash_password, issue_token, verify_password)
from app.core.config import settings
from app.db.session import SessionFactory
from app.models import UserAccountRecord


router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/mode")
async def authentication_mode() -> dict[str, bool]:
    return {"required": not settings.is_local_demo}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=190)
    password: SecretStr


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=190)
    password: SecretStr
    role: str = "user"


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 1800
    user_id: str
    username: str
    role: str


@router.post("/login", response_model=SessionResponse)
async def login(payload: LoginRequest) -> SessionResponse:
    if not settings.auth_secret_key or len(settings.auth_secret_key) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured.")
    try:
        async with SessionFactory() as session:
            user = await session.scalar(select(UserAccountRecord).where(
                UserAccountRecord.username == payload.username.strip().lower()))
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Authentication service is unavailable.") from error
    if user is None or not user.is_active or not verify_password(payload.password.get_secret_value(),
                                                                  user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Username or password is incorrect.",
                            headers={"WWW-Authenticate": "Bearer"})
    principal = Principal(user_id=user.user_id, username=user.username, role=user.role)
    return SessionResponse(access_token=issue_token(principal, settings.auth_secret_key),
                           user_id=principal.user_id, username=principal.username, role=principal.role)


@router.get("/me")
async def current_user(principal: Principal = Depends(current_principal)) -> dict[str, str]:
    return {"user_id": principal.user_id, "username": principal.username, "role": principal.role}


@router.post("/logout", status_code=204)
async def logout(_principal: Principal = Depends(current_principal)) -> None:
    # Bearer sessions are short-lived and stateless; the client discards its token.
    return None


@router.post("/users", status_code=201)
async def create_user(payload: UserCreateRequest,
                      _admin: Principal = Depends(admin_principal)) -> dict[str, str]:
    username = payload.username.strip().lower()
    if payload.role not in {"user", "admin"}:
        raise HTTPException(status_code=422, detail="Role must be user or admin.")
    try:
        encoded_password = hash_password(payload.password.get_secret_value())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    user_id = str(uuid4())
    try:
        async with SessionFactory.begin() as session:
            if await session.scalar(select(UserAccountRecord.user_id).where(
                    UserAccountRecord.username == username)):
                raise HTTPException(status_code=409, detail="Username already exists.")
            session.add(UserAccountRecord(user_id=user_id, username=username,
                                          password_hash=encoded_password, role=payload.role,
                                          is_active=True, created_at=datetime.now(timezone.utc)))
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="User provisioning failed.") from error
    return {"user_id": user_id, "username": username, "role": payload.role}
