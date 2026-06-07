"""Supabase JWT verification.

Two verification paths share a single code path (HS256 is NOT a "fallback" —
it is a first-class branch chosen by the token's ``alg`` header):

1. **RS256** — production Supabase access tokens. We fetch the project JWKS
   (``{supabase_url}/auth/v1/.well-known/jwks.json``) with httpx, cache the
   ``PyJWKClient``, and verify the signature against the matching ``kid``.

2. **HS256** — local/test tokens minted with ``settings.supabase_jwt_secret``
   (the project's JWT secret). Enabled only when that secret is configured.
   This is how the test-suite mints tokens; it is intentional, not degraded.

Claims extracted: ``sub`` -> ``user_id``, plus ``org_id`` and ``role``. Supabase
nests custom claims under ``app_metadata`` / ``user_metadata`` depending on how
they were set, so we look in a few well-known locations. ``org_id`` may be
absent on a first sign-in (before the org is provisioned); in that case it is
left as ``None`` and the dependency layer bootstraps the org/membership and
backfills it.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import jwt
from jwt import InvalidTokenError, PyJWKClient
from pydantic import BaseModel, Field

from relay.config import settings

logger = logging.getLogger("relay.auth.jwt")

# Supabase signs access tokens with the "authenticated" audience by default.
_DEFAULT_AUDIENCE = "authenticated"


class Claims(BaseModel):
    """Verified, normalized JWT claims used across the backend.

    ``org_id`` is optional at the wire level (a brand-new user may not yet be
    attached to an org); :mod:`relay.auth.deps` is responsible for ensuring a
    concrete ``org_id`` before any tenant-scoped work happens. Everywhere a
    request actually touches tenant data, ``org_id`` is populated.
    """

    user_id: str = Field(..., description="Supabase auth subject (sub).")
    org_id: str | None = Field(
        default=None, description="Tenant/organization id (uuid str)."
    )
    role: str = Field(default="member", description="Application role within the org.")

    # Raw verified payload, for downstream needs (email, metadata). Never logged.
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def email(self) -> str | None:
        return self.raw.get("email")


class AuthError(Exception):
    """Raised when a token cannot be verified. Mapped to a 401 by the deps layer."""


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    """Build (and cache) the JWKS client for RS256 verification.

    PyJWKClient caches fetched signing keys internally; we additionally cache the
    client itself so we only construct/fetch once per process. ``lru_cache`` makes
    this lazy — importing this module never performs network I/O.
    """
    if not settings.supabase_url:
        raise AuthError("supabase_url is not configured; cannot verify RS256 tokens")
    base = settings.supabase_url.rstrip("/")
    jwks_url = f"{base}/auth/v1/.well-known/jwks.json"
    return PyJWKClient(jwks_url, cache_keys=True)


def _issuer() -> str | None:
    """Expected ``iss`` claim, if known/configured."""
    if settings.supabase_jwt_issuer:
        return settings.supabase_jwt_issuer
    if settings.supabase_url:
        return f"{settings.supabase_url.rstrip('/')}/auth/v1"
    return None


def _extract_org_id(payload: dict[str, Any]) -> str | None:
    """Pull ``org_id`` from the well-known locations Supabase may carry it in."""
    for source in (
        payload,
        payload.get("app_metadata") or {},
        payload.get("user_metadata") or {},
    ):
        if isinstance(source, dict):
            val = source.get("org_id") or source.get("organization_id")
            if val:
                return str(val)
    return None


def _extract_role(payload: dict[str, Any]) -> str:
    """Pull the application role; default to ``member``.

    Prefer an explicit app-level role in metadata over the Supabase ``role``
    claim (which is the Postgres role, usually ``authenticated``).
    """
    for source in (
        payload.get("app_metadata") or {},
        payload.get("user_metadata") or {},
        payload,
    ):
        if isinstance(source, dict):
            val = source.get("app_role") or source.get("relay_role")
            if val:
                return str(val)
    # Fall back to the standard role claim only if it is an app role, not the
    # Postgres ``authenticated``/``anon`` role.
    role = payload.get("role")
    if isinstance(role, str) and role not in ("authenticated", "anon", ""):
        return role
    return "member"


def _decode(token: str) -> dict[str, Any]:
    """Verify ``token`` and return its raw payload, choosing alg by header.

    Audience verification is relaxed to the default ``authenticated`` audience
    but tolerant of its absence; issuer is verified when known.
    """
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as exc:  # malformed token
        raise AuthError(f"malformed token: {exc}") from exc

    alg = header.get("alg")
    issuer = _issuer()

    # Common options. We require exp; aud is verified leniently below.
    options = {"require": ["exp"], "verify_aud": False}

    if alg == "HS256":
        secret = settings.supabase_jwt_secret
        if not secret:
            raise AuthError("HS256 token received but supabase_jwt_secret is not set")
        try:
            return jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                options=options,
                issuer=issuer,
            )
        except InvalidTokenError as exc:
            raise AuthError(f"HS256 verification failed: {exc}") from exc

    if alg == "RS256":
        try:
            signing_key = _jwks_client().get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                options=options,
                issuer=issuer,
            )
        except AuthError:
            raise
        except InvalidTokenError as exc:
            raise AuthError(f"RS256 verification failed: {exc}") from exc
        except Exception as exc:  # JWKS fetch / key errors
            raise AuthError(f"could not verify RS256 token: {exc}") from exc

    raise AuthError(f"unsupported token alg: {alg!r}")


async def verify_token(token: str) -> Claims:
    """Verify a Supabase JWT and return normalized :class:`Claims`.

    Accepts both RS256 (production, via cached JWKS) and HS256 (local/test, via
    ``settings.supabase_jwt_secret``) on the same code path, dispatched by the
    token's ``alg`` header. Raises :class:`AuthError` on any failure.

    Declared ``async`` to satisfy the contract and FastAPI dependency usage;
    PyJWT's verification (and PyJWKClient's internally-cached, synchronous JWKS
    fetch) is fast and non-blocking for the cached path.
    """
    if not token or not isinstance(token, str):
        raise AuthError("missing token")
    # Tolerate a stray "Bearer " prefix if a caller passes the raw header value.
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    payload = _decode(token)

    sub = payload.get("sub")
    if not sub:
        raise AuthError("token missing 'sub' claim")

    return Claims(
        user_id=str(sub),
        org_id=_extract_org_id(payload),
        role=_extract_role(payload),
        raw=payload,
    )


__all__ = ["Claims", "AuthError", "verify_token"]
