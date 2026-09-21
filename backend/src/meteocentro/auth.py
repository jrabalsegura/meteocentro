"""Revocable opaque sessions, explicit origins and PostgreSQL login limits."""

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from meteocentro.config import get_settings
from meteocentro.db import get_session
from meteocentro.job_queue import db_now
from meteocentro.models import AdminSession, AdminUser, CatalogVersion, LoginThrottle

router = APIRouter(prefix="/api/v1/auth", tags=["access"])
Db = Annotated[Session, Depends(get_session)]


def cookie_name():
    return "__Host-meteocentro" if get_settings().environment == "production" else "meteocentro"


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(token):
    return hmac.new(token.encode(), b"meteocentro-csrf-v1", hashlib.sha256).hexdigest()


def password_hash(password, *, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=2**17,
        r=8,
        p=1,
        maxmem=256 * 1024 * 1024,
        dklen=32,
    ).hex()
    return f"scrypt$131072$8$1${salt}${digest}"


def password_matches(password, encoded):
    # Identical work for unknown accounts; only this version's bounded format is accepted.
    parts = (encoded or "").split("$")
    valid = len(parts) == 6 and parts[:4] == ["scrypt", "131072", "8", "1"]
    salt = parts[4] if valid else "00" * 16
    try:
        candidate = password_hash(password, salt=salt)
    except ValueError:
        candidate = password_hash(password, salt="00" * 16)
        valid = False
    return hmac.compare_digest(candidate, encoded or "") and valid


def check_origin(request: Request):
    if (
        request.headers.get("origin") != get_settings().app_origin
        or request.headers.get("sec-fetch-site") == "cross-site"
        or request.headers.get("content-type", "").split(";")[0].strip() != "application/json"
    ):
        raise HTTPException(403, detail={"code": "origin_rejected"})


def current_session(request: Request, db: Session):
    token = request.cookies.get(cookie_name(), "")
    if len(token) != 43:
        return None
    return db.execute(
        select(AdminSession, AdminUser)
        .join(AdminUser)
        .where(
            AdminSession.token_hash == token_hash(token),
            AdminSession.revoked_at.is_(None),
            AdminSession.expires_at > db_now(db),
            AdminUser.enabled.is_(True),
        )
    ).first()


def require_admin(request: Request, db: Db):
    row = current_session(request, db)
    if row is None:
        raise HTTPException(401, detail={"code": "authentication_required"})
    session, user = row
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        expected = csrf_token(request.cookies[cookie_name()])
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), expected):
            raise HTTPException(403, detail={"code": "csrf_rejected"})
    request.state.admin_session = session
    return user


Admin = Annotated[AdminUser, Depends(require_admin)]


def require_reader(request: Request, db: Db):
    if not request.url.path.startswith("/api/v1/") or request.url.path.startswith("/api/v1/auth/"):
        return
    if get_settings().private_read:
        if current_session(request, db) is None:
            raise HTTPException(401, detail={"code": "authentication_required"})
    request.state.catalog_version = (
        db.scalar(select(CatalogVersion.version).where(CatalogVersion.id == 1)) or 0
    )


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    username: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: SecretStr = Field(min_length=1, max_length=1024)


def reserve_login(db, username, address):
    now = db_now(db)
    # Never trust X-Forwarded-For supplied by a client. Proxy trust is configured at deployment.
    buckets = {
        token_hash("user:" + username): 5,
        token_hash("ip:" + address): 20,
        token_hash("global"): 60,
    }
    db.execute(delete(LoginThrottle).where(LoginThrottle.started_at < now - timedelta(days=1)))
    rejected = False
    for key in sorted(buckets):
        db.execute(
            insert(LoginThrottle)
            .values(key=key, started_at=now, attempts=0)
            .on_conflict_do_nothing()
        )
        bucket = db.scalar(select(LoginThrottle).where(LoginThrottle.key == key).with_for_update())
        if bucket.started_at <= now - timedelta(minutes=15):
            bucket.started_at, bucket.attempts = now, 0
        rejected |= bucket.attempts >= buckets[key]
        bucket.attempts += 1
    db.commit()  # Failed logins and concurrent attempts also consume the durable budget.
    if rejected:
        raise HTTPException(429, detail={"code": "login_limited"}, headers={"Retry-After": "900"})


@router.post("/login")
def login(body: Credentials, request: Request, response: Response, db: Db):
    check_origin(request)
    reserve_login(db, body.username, request.client.host if request.client else "unknown")
    user = db.scalar(select(AdminUser).where(AdminUser.username == body.username))
    if (
        not password_matches(body.password.get_secret_value(), user.password_hash if user else None)
        or not user
        or not user.enabled
    ):
        raise HTTPException(401, detail={"code": "invalid_credentials"})
    token = secrets.token_urlsafe(32)
    expiry = db_now(db) + timedelta(hours=get_settings().session_hours)
    # Rotate an existing browser session without revoking sessions on other devices.
    old = current_session(request, db)
    if old:
        old[0].revoked_at = db_now(db)
    db.add(AdminSession(user_id=user.id, token_hash=token_hash(token), expires_at=expiry))
    db.commit()
    response.set_cookie(
        cookie_name(),
        token,
        max_age=get_settings().session_hours * 3600,
        httponly=True,
        secure=get_settings().environment == "production",
        samesite="strict",
        path="/",
    )
    return {
        "authenticated": True,
        "username": user.username,
        "csrf_token": csrf_token(token),
        "expires_at": expiry,
        "private_read": get_settings().private_read,
    }


@router.get("/session")
def session_info(request: Request, db: Db):
    row = current_session(request, db)
    result = {"authenticated": bool(row), "private_read": get_settings().private_read}
    if row:
        result.update(
            username=row[1].username,
            expires_at=row[0].expires_at,
            csrf_token=csrf_token(request.cookies[cookie_name()]),
        )
    return result


@router.post("/logout")
def logout(request: Request, response: Response, db: Db, user: Admin):
    request.state.admin_session.revoked_at = db_now(db)
    db.commit()
    response.delete_cookie(
        cookie_name(),
        path="/",
        httponly=True,
        secure=get_settings().environment == "production",
        samesite="strict",
    )
    return {"authenticated": False}


@router.post("/revoke-all")
def revoke_all(db: Db, user: Admin):
    db.execute(
        update(AdminSession)
        .where(AdminSession.user_id == user.id, AdminSession.revoked_at.is_(None))
        .values(revoked_at=db_now(db))
    )
    db.commit()
    return {"authenticated": False}
