"""
Authentication helpers for Digarr.

Supports two modes (either, both, or neither):
  - Password: set via Settings UI (bcrypt hash stored in config.json), or
              DIGARR_PASSWORD env var as a fallback (plain-text comparison).
              Config password takes precedence over the env var.
  - OIDC: oidc_issuer + oidc_client_id + oidc_client_secret in config.json

Sessions are stored in-memory (cleared on container restart).
No max-age on the session cookie → expires when the browser closes.
"""

import os
import secrets
from typing import Optional

import bcrypt
import httpx

from database import db_save_session, db_is_valid_session, db_revoke_session, save_oauth_state, consume_oauth_state


def generate_session() -> str:
    token = secrets.token_hex(32)
    db_save_session(token)
    return token


def is_valid_session(token: Optional[str]) -> bool:
    return db_is_valid_session(token)


def revoke_session(token: str) -> None:
    db_revoke_session(token)


# ---------------------------------------------------------------------------
# Password auth
# ---------------------------------------------------------------------------

def _env_password() -> str:
    return os.environ.get("DIGARR_PASSWORD", "").strip()


def hash_password(password: str) -> str:
    """Return a bcrypt hash of the given password."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def check_credentials(username: str, password: str, config: dict) -> bool:
    """
    Validate username + password against stored credentials.
    Config-stored bcrypt hash takes precedence over the DIGARR_PASSWORD env var.
    Username check uses compare_digest to avoid timing attacks.
    If no username is configured, the username field is ignored.
    """
    stored_username = config.get("auth_username", "").strip()
    if stored_username and not secrets.compare_digest(username, stored_username):
        return False

    hashed = config.get("hashed_password", "")
    if hashed:
        try:
            return bcrypt.checkpw(password.encode(), hashed.encode())
        except Exception:
            return False
    # Fall back to env var (plain-text comparison)
    pw = _env_password()
    if not pw:
        return False
    return secrets.compare_digest(password, pw)



def password_source(config: dict) -> Optional[str]:
    """Return 'config', 'env', or None — where the active password comes from."""
    if config.get("hashed_password"):
        return "config"
    if _env_password():
        return "env"
    return None


# ---------------------------------------------------------------------------
# Auth status helpers
# ---------------------------------------------------------------------------

def auth_methods(config: dict) -> list[str]:
    """Return which auth methods are active based on env + config."""
    methods = []
    if config.get("hashed_password") or _env_password():
        methods.append("password")
    if config.get("oidc_issuer") and config.get("oidc_client_id") and config.get("oidc_client_secret"):
        methods.append("oidc")
    return methods


def auth_required(config: dict) -> bool:
    return bool(auth_methods(config))


# ---------------------------------------------------------------------------
# OIDC helpers
# ---------------------------------------------------------------------------

_oidc_discovery_cache: dict[str, tuple] = {}  # issuer → (data, expires_at)
_OIDC_CACHE_TTL = 3600  # seconds


async def get_oidc_discovery(issuer: str) -> dict:
    """Fetch and cache the OIDC discovery document with a 1-hour TTL."""
    import time
    cached = _oidc_discovery_cache.get(issuer)
    if cached and time.monotonic() < cached[1]:
        return cached[0]
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
    data = r.json()
    _oidc_discovery_cache[issuer] = (data, time.monotonic() + _OIDC_CACHE_TTL)
    return data


def generate_oidc_state() -> str:
    """Generate a one-time CSRF state token for an OIDC flow, persisted to DB."""
    state = secrets.token_hex(16)
    save_oauth_state(state, flow="oidc")
    return state


def consume_oidc_state(state: str) -> bool:
    """Validate and consume a state token. Returns False if unknown or expired."""
    if not state:
        return False
    return consume_oauth_state(state, flow="oidc") is not None


async def get_user_info(access_token: str, issuer: str) -> dict:
    """Call the userinfo endpoint to confirm the access token is valid."""
    discovery = await get_oidc_discovery(issuer)
    userinfo_endpoint = discovery.get("userinfo_endpoint")
    if not userinfo_endpoint:
        raise ValueError("No userinfo_endpoint in OIDC discovery document")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}"})
        r.raise_for_status()
    return r.json()


async def exchange_code(config: dict, code: str) -> dict:
    """
    Exchange an authorization code for tokens at the provider's token endpoint.
    Returns the raw token response dict on success, raises on failure.
    """
    discovery = await get_oidc_discovery(config["oidc_issuer"])
    token_endpoint = discovery["token_endpoint"]

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(token_endpoint, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.get("oidc_redirect_uri", ""),
            "client_id": config["oidc_client_id"],
            "client_secret": config["oidc_client_secret"],
        })

    if r.status_code != 200:
        raise ValueError(f"Token exchange failed ({r.status_code}): {r.text}")

    return r.json()
