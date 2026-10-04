"""Password hashing and short-lived HMAC bearer sessions (stdlib only)."""

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

_bearer = HTTPBearer(auto_error=False)
_TOKEN_TTL_SECONDS = 1800


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Passwords must be at least 12 characters long.")
    salt = os.urandom(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + _b64(salt) + "$" + _b64(derived)


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r),
                                p=int(p), dklen=len(_unb64(expected)))
        return hmac.compare_digest(actual, _unb64(expected))
    except (ValueError, TypeError):
        return False


def issue_token(principal: Principal, secret: str, *, now: int | None = None) -> str:
    if len(secret) < 32:
        raise ValueError("AUTH_SECRET_KEY must contain at least 32 characters.")
    timestamp = int(time.time()) if now is None else now
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps({"sub": principal.user_id, "name": principal.username,
                               "role": principal.role, "exp": timestamp + _TOKEN_TTL_SECONDS,
                               "iat": timestamp}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    signature = _b64(hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest())
    return f"{signing_input}.{signature}"


def decode_token(token: str, secret: str, *, now: int | None = None) -> Principal:
    try:
        header, payload, signature = token.split(".", 2)
        signing_input = f"{header}.{payload}"
        expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise ValueError("Invalid token signature")
        metadata = json.loads(_unb64(header))
        claims = json.loads(_unb64(payload))
        timestamp = int(time.time()) if now is None else now
        if metadata.get("alg") != "HS256" or int(claims["exp"]) <= timestamp:
            raise ValueError("Expired or unsupported token")
        role = claims["role"]
        if role not in {"user", "admin"}:
            raise ValueError("Unsupported role")
        return Principal(user_id=str(claims["sub"]), username=str(claims["name"]), role=role)
    except Exception as error:
        raise ValueError("Invalid or expired access token") from error


async def current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if settings.app_env.lower() == "development" and credentials is None:
        return Principal(user_id="local-demo", username="local-demo", role="admin")
    if not settings.auth_secret_key or len(settings.auth_secret_key) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured.")
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"})
    try:
        principal = decode_token(credentials.credentials, settings.auth_secret_key)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or expired session.", headers={"WWW-Authenticate": "Bearer"}) from error
    request.state.principal = principal
    return principal


async def admin_principal(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required.")
    return principal


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
