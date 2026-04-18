from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Literal
import uvicorn
import asyncio
import io
import logging
import os
import re
import traceback
import uuid
from datetime import datetime

import json as _json

if os.environ.get("LOG_FORMAT", "").lower() == "json":
    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            entry = {
                "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                entry["exc"] = self.formatException(record.exc_info)
            return _json.dumps(entry)

    _handler = logging.StreamHandler()
    _handler.setFormatter(_JsonFormatter())
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(_handler)
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
logger = logging.getLogger(__name__)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from database import (
    init_db, save_playlist, get_playlists, get_playlist,
    update_playlist_plex_result, update_playlist, update_playlist_tracks, update_playlist_import_results,
    update_playlist_spotify_result,
    update_playlist_jellyfin_result, update_playlist_navidrome_result,
    touch_playlist_refreshed, delete_playlist, rename_playlist, set_playlist_merge_tracks,
    db_delete_import_jobs_for_playlist, db_delete_import_job,
    get_all_playlist_artist_names,
    # track_cache
    db_upsert_track_cache, db_search_track_cache, db_get_cache_stats, db_lookup_track_cache,
    # manual matches
    db_set_manual_match, db_get_manual_matches, _norm as _db_norm,
    # ignored tracks
    db_ignore_track, db_unignore_track, db_get_ignored_tracks,
    # download client caches
    db_upsert_download_search_cache, db_get_download_search_cache,
    db_upsert_download_queue_cache, db_get_download_queue_cache,
)
from lidarr import LidarrClient
from musicbrainz import lookup_track as mb_lookup_track
from plex import PlexClient
from jellyfin import JellyfinClient
from navidrome import NavidromeClient
from media_client import PlexMediaClient, JellyfinMediaClient, NavidromeMediaClient
from download_client import LidarrDownloadClient
from deemix import DeemixClient
from slskd import SlskdClient
from ai.claude import ClaudeProvider
from ai.openai import OpenAIProvider


def make_ai_provider(config: dict):
    provider = config.get("active_ai_provider", "claude")
    if provider == "openai":
        api_key = config.get("openai_api_key", "")
        if not api_key:
            raise ValueError("OpenAI API key not configured")
        return OpenAIProvider(api_key, model=config.get("openai_model", "gpt-4o-mini"))
    else:
        api_key = config.get("anthropic_api_key", "")
        if not api_key:
            raise ValueError("Anthropic API key not configured")
        return ClaudeProvider(api_key, model=config.get("claude_model", "claude-sonnet-4-6"))
from auth import (
    auth_required as _auth_required,
    auth_methods as _auth_methods,
    generate_session,
    is_valid_session,
    revoke_session,
    check_credentials,
    hash_password,
    password_source as _password_source,
    generate_oidc_state,
    consume_oidc_state,
    exchange_code,
    get_oidc_discovery,
    get_user_info,
)
from config import load_config, save_config
from database import (
    db_prune_expired_sessions, save_oauth_state, consume_oauth_state,
    db_save_import_job, db_load_recent_import_jobs, db_prune_import_jobs,
    update_playlist_deemix_result, update_playlist_slskd_result,
    db_increment_stat, db_set_stat_text, db_set_stat_text_if_unset, db_get_all_stats,
)
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from parsers.m3u import parse_m3u_content
from parsers.text import fetch_url_content
from spotify import (
    extract_playlist_id, get_access_token, fetch_playlist,
    generate_pkce_pair, get_oauth_token, exchange_code as spotify_exchange_code,
    get_current_user as spotify_get_current_user,
    get_all_playlists, fetch_liked_songs, push_to_spotify,
)
from listenbrainz import get_recommendation_playlist as lb_recommendation
from lastfm import get_similar_artists as lfm_get_similar_artists
from discogs import get_wantlist as discogs_get_wantlist
from utils import deduplicate_artists, normalize
import httpx

def _real_ip(request: Request) -> str:
    return (
        request.headers.get("x-real-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )


limiter = Limiter(key_func=_real_ip)
app = FastAPI(title="Digarr", version="1.0.0")

_jobs: dict = {}
_MAX_COMPLETED_JOBS = 30
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        raise exc
    logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=True)
    return JSONResponse({"detail": "An unexpected error occurred."}, status_code=500)


def _is_https(request: Request) -> bool:
    """True when the request arrived over HTTPS (via reverse proxy)."""
    return request.headers.get("x-forwarded-proto", "http").lower() == "https"
scheduler = AsyncIOScheduler()


async def _send_webhook(url: str, payload: dict) -> None:
    """POST a JSON payload to the configured webhook URL. Best-effort — never raises."""
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as exc:
        logger.warning("Webhook delivery failed: %s", exc)


def make_lidarr_client(config: dict) -> LidarrClient:
    return LidarrClient(
        config["lidarr_url"],
        config["lidarr_api_key"],
        quality_profile_id=int(config.get("lidarr_quality_profile_id", 1)),
        metadata_profile_id=int(config.get("lidarr_metadata_profile_id", 1)),
        root_folder=config.get("lidarr_root_folder", "/music"),
    )

def _plex_playlist_name(name: str, config: dict) -> str:
    if config.get("plex_append_digarr", True):
        return f"{name} \u2014 Digarr"
    return name

def _jellyfin_playlist_name(name: str, config: dict) -> str:
    if config.get("jellyfin_append_digarr", False):
        return f"{name} \u2014 Digarr"
    return name

def _navidrome_playlist_name(name: str, config: dict) -> str:
    if config.get("navidrome_append_digarr", False):
        return f"{name} \u2014 Digarr"
    return name


_cors_origins_env = os.environ.get("DIGARR_CORS_ORIGINS", "*").strip()
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth-exempt paths — these never require a session
_AUTH_EXEMPT = {"/api/auth/status", "/api/auth/login"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # OIDC callback + status + login are always open
    if path.startswith("/auth/oidc/") or path.startswith("/auth/spotify/") or path in _AUTH_EXEMPT:
        return await call_next(request)
    # Only protect /api/* routes
    if path.startswith("/api/"):
        config = load_config()
        if _auth_required(config):
            session_token = request.cookies.get("digarr_session")
            if not is_valid_session(session_token):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return await call_next(request)

@app.on_event("startup")
async def startup():
    init_db()
    db_prune_expired_sessions()
    # Recover persisted jobs; drop any that were mid-flight (unrecoverable)
    for job in db_load_recent_import_jobs():
        if job["status"] in ("running", "queued"):
            db_delete_import_job(job["id"])
        else:
            _jobs[job["id"]] = job
    config = load_config()
    _reschedule(int(config.get("refresh_interval_hours") or 0))
    _reschedule_plex_sync(int(config.get("plex_sync_interval_hours") or 0))
    _reschedule_jellyfin_sync(int(config.get("jellyfin_sync_interval_hours") or 0))
    _reschedule_navidrome_sync(int(config.get("navidrome_sync_interval_hours") or 0))
    scheduler.start()

@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=False)

# --- Config ---

@app.get("/api/config")
def get_config():
    return load_config()

# --- Auth ---

@app.get("/api/auth/status")
def get_auth_status(request: Request):
    config = load_config()
    methods = _auth_methods(config)
    required = bool(methods)
    session_token = request.cookies.get("digarr_session")
    authenticated = is_valid_session(session_token) if required else True
    return {
        "auth_required": required,
        "authenticated": authenticated,
        "methods": methods,
        "password_source": _password_source(config),
        "username": config.get("auth_username", "").strip() or None,
    }

class LoginRequest(BaseModel):
    username: str = Field(default="", max_length=200)
    password: str = Field(max_length=1000)

@app.post("/api/auth/login")
@limiter.limit("10/minute")
def login(req: LoginRequest, request: Request, response: Response):
    config = load_config()
    if not check_credentials(req.username, req.password, config):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = generate_session()
    response.set_cookie(
        key="digarr_session",
        value=token,
        httponly=True,
        samesite="lax",
        secure=_is_https(request),
    )
    return {"ok": True}

class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=1000)

@app.post("/api/auth/set-password")
def set_password_route(req: SetPasswordRequest):
    if not req.password or len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    save_config({"hashed_password": hash_password(req.password)})
    return {"ok": True}

@app.delete("/api/auth/password")
def clear_password_route():
    save_config({"hashed_password": ""})
    return {"ok": True}

@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("digarr_session")
    if token:
        revoke_session(token)
    response.delete_cookie("digarr_session")
    return {"ok": True}

@app.get("/auth/oidc/start")
async def oidc_start():
    config = load_config()
    if not config.get("oidc_issuer") or not config.get("oidc_client_id"):
        raise HTTPException(status_code=400, detail="OIDC not configured")
    discovery = await get_oidc_discovery(config["oidc_issuer"])
    auth_endpoint = discovery["authorization_endpoint"]
    state = generate_oidc_state()
    import urllib.parse
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": config["oidc_client_id"],
        "redirect_uri": config.get("oidc_redirect_uri", ""),
        "scope": "openid email profile",
        "state": state,
    })
    return RedirectResponse(f"{auth_endpoint}?{params}", status_code=302)

@app.get("/auth/oidc/callback")
async def oidc_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/?oidc_error={error}", status_code=302)
    if not consume_oidc_state(state):
        return RedirectResponse("/?oidc_error=invalid_state", status_code=302)
    config = load_config()
    try:
        token_data = await exchange_code(config, code)
        access_token = token_data.get("access_token")
        if not access_token:
            return RedirectResponse("/?oidc_error=no_access_token", status_code=302)
        user_info = await get_user_info(access_token, config["oidc_issuer"])
        allowed = [e.strip().lower() for e in (config.get("oidc_allowed_emails") or []) if e.strip()]
        if allowed and user_info.get("email", "").lower() not in allowed:
            return RedirectResponse("/?oidc_error=unauthorized", status_code=302)
    except Exception:
        return RedirectResponse("/?oidc_error=exchange_failed", status_code=302)
    token = generate_session()
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        key="digarr_session",
        value=token,
        httponly=True,
        samesite="lax",
        secure=_is_https(request),
    )
    return response

# --- Spotify OAuth ---

@app.get("/auth/spotify/start")
async def spotify_oauth_start():
    config = load_config()
    client_id = config.get("spotify_client_id", "").strip()
    redirect_uri = config.get("spotify_redirect_uri", "").strip()
    if not client_id or not redirect_uri:
        raise HTTPException(status_code=400, detail="Spotify client ID and redirect URI must be set in Settings before connecting.")

    verifier, challenge = generate_pkce_pair()
    import secrets as _secrets
    state = _secrets.token_urlsafe(16)
    save_oauth_state(state, flow="spotify", verifier=verifier)

    import urllib.parse
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "playlist-read-private playlist-read-collaborative playlist-modify-public playlist-modify-private user-library-read",
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    })
    return RedirectResponse(f"https://accounts.spotify.com/authorize?{params}", status_code=302)


@app.get("/auth/spotify/callback")
async def spotify_oauth_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/settings?spotify_error={error}", status_code=302)

    verifier = consume_oauth_state(state, flow="spotify")
    if not verifier:
        return RedirectResponse("/settings?spotify_error=invalid_state", status_code=302)

    config = load_config()
    client_id = config.get("spotify_client_id", "").strip()
    redirect_uri = config.get("spotify_redirect_uri", "").strip()

    try:
        token_data = await spotify_exchange_code(client_id, code, redirect_uri, verifier)
        user = await spotify_get_current_user(token_data["spotify_access_token"])
        save_config({
            **token_data,
            "spotify_user_id": user["id"],
            "spotify_display_name": user["display_name"],
        })
    except Exception:
        return RedirectResponse("/settings?spotify_error=exchange_failed", status_code=302)

    return RedirectResponse("/settings", status_code=302)


@app.get("/api/spotify/status")
def spotify_status():
    config = load_config()
    connected = bool(config.get("spotify_access_token") or config.get("spotify_refresh_token"))
    return {
        "connected": connected,
        "display_name": config.get("spotify_display_name", "") if connected else "",
        "user_id": config.get("spotify_user_id", "") if connected else "",
    }


@app.delete("/api/spotify/disconnect")
def spotify_disconnect():
    save_config({
        "spotify_access_token": "",
        "spotify_refresh_token": "",
        "spotify_token_expires_at": None,
        "spotify_user_id": "",
        "spotify_display_name": "",
    })
    return {"ok": True}


@app.get("/api/spotify/playlists")
async def spotify_playlists(filter: str | None = None):
    """
    filter=user  — playlists owned by the current user + Liked Songs
    omit         — all playlists
    """
    config = load_config()
    token = await get_oauth_token(config)
    if not token:
        raise HTTPException(status_code=400, detail="Spotify account not connected. Go to Settings → Spotify and click Connect.")
    try:
        playlists = await get_all_playlists(token)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=400, detail=f"Spotify API error ({e.response.status_code}).")
    if filter == "user":
        playlists = [p for p in playlists if p["type"] in ("user", "liked_songs")]
    return {"playlists": playlists}


@app.get("/api/spotify/playlist/{playlist_id}")
async def spotify_fetch_playlist(playlist_id: str):
    config = load_config()
    # Try OAuth token first, fall back to client credentials
    token = await get_oauth_token(config)
    if not token:
        client_id = config.get("spotify_client_id", "")
        client_secret = config.get("spotify_client_secret", "")
        if not client_id or not client_secret:
            raise HTTPException(status_code=400, detail="Spotify not configured.")
        token = await get_access_token(client_id, client_secret)

    if playlist_id == "liked_songs":
        data = await fetch_liked_songs(token)
    else:
        data = await fetch_playlist(playlist_id, token)

    return data


@app.post("/api/spotify/push/{playlist_id}")
async def spotify_push_playlist(playlist_id: int):
    config = load_config()
    token = await get_oauth_token(config)
    if not token:
        raise HTTPException(status_code=400, detail="Spotify account not connected.")
    user_id = config.get("spotify_user_id", "")
    if not user_id:
        raise HTTPException(status_code=400, detail="Spotify user ID not found. Reconnect in Settings.")

    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found.")

    tracks = pl.get("tracks") or []
    if isinstance(tracks, str):
        import json
        tracks = json.loads(tracks)

    existing_spotify_id = pl.get("spotify_playlist_id") or None
    try:
        result = await push_to_spotify(
            user_id=user_id,
            name=pl["name"],
            tracks=tracks,
            token=token,
            existing_playlist_id=existing_spotify_id,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=400, detail=f"Spotify API error ({e.response.status_code}).")

    update_playlist_spotify_result(
        playlist_id,
        result["playlist_id"],
        result["matched_count"],
        result["total_count"],
    )
    return result


# --- Config ---

@app.post("/api/config")
def update_config(config: dict):
    save_config(config)
    _reschedule(int(config.get("refresh_interval_hours") or 0))
    _reschedule_plex_sync(int(config.get("plex_sync_interval_hours") or 0))
    _reschedule_jellyfin_sync(int(config.get("jellyfin_sync_interval_hours") or 0))
    _reschedule_navidrome_sync(int(config.get("navidrome_sync_interval_hours") or 0))
    return {"status": "ok"}

def _reschedule(hours: int):
    if scheduler.get_job("refresh_all"):
        scheduler.remove_job("refresh_all")
    if hours > 0:
        scheduler.add_job(
            _refresh_all_playlists_job,
            IntervalTrigger(hours=hours),
            id="refresh_all",
            replace_existing=True,
        )


def _reschedule_plex_sync(hours: int):
    if scheduler.get_job("plex_sync_all"):
        scheduler.remove_job("plex_sync_all")
    if hours > 0:
        scheduler.add_job(_plex_sync_all_job, IntervalTrigger(hours=hours), id="plex_sync_all", replace_existing=True)

def _reschedule_jellyfin_sync(hours: int):
    if scheduler.get_job("jellyfin_sync_all"):
        scheduler.remove_job("jellyfin_sync_all")
    if hours > 0:
        scheduler.add_job(_jellyfin_sync_all_job, IntervalTrigger(hours=hours), id="jellyfin_sync_all", replace_existing=True)

def _reschedule_navidrome_sync(hours: int):
    if scheduler.get_job("navidrome_sync_all"):
        scheduler.remove_job("navidrome_sync_all")
    if hours > 0:
        scheduler.add_job(_navidrome_sync_all_job, IntervalTrigger(hours=hours), id="navidrome_sync_all", replace_existing=True)

async def _refresh_all_playlists_job() -> dict:
    logger.info("Scheduler: starting scheduled refresh")
    config = load_config()
    playlists = get_playlists()
    excluded = set(config.get("refresh_excluded_playlist_ids") or [])
    refreshable = [
        pl for pl in playlists
        if pl.get("source_url")
        and pl.get("source_type") in ("url", "m3u_url", "listenbrainz", "similar", "discogs", "spotify")
        and pl["id"] not in excluded
    ]
    delay = int(config.get("refresh_delay_between_playlists") or 0)
    max_new = int(config.get("refresh_max_new_artists") or 0)
    total_new_artists = 0
    summary = []
    for i, pl in enumerate(refreshable):
        if max_new and total_new_artists >= max_new:
            logger.info("Scheduler: max new artists (%s) reached, stopping early", max_new)
            break
        if delay and i > 0:
            await asyncio.sleep(delay)
        try:
            result = await _do_refresh_playlist(pl["id"])
            added = result["new_artists_added"]
            total_new_artists += added
            summary.append({
                "name": pl["name"],
                "new_artists": added,
                "total_tracks": result["total_tracks"],
                "status": "ok",
            })
            logger.info("Scheduler: refreshed '%s' — %s new artists", pl['name'], added)
        except Exception as e:
            tb = traceback.format_exc()
            msg = str(e) or type(e).__name__
            summary.append({"name": pl["name"], "status": "error", "error": msg})
            logger.error("Scheduler: error refreshing '%s': %s", pl['name'], msg)
    last_run = datetime.utcnow().isoformat()
    save_config({**config, "refresh_last_run": last_run, "refresh_last_run_summary": summary})
    db_increment_stat("refresh_runs_total")
    db_increment_stat("artists_added_via_refresh_total", total_new_artists)
    webhook_url = config.get("webhook_url", "")
    if webhook_url:
        changes_only = config.get("refresh_webhook_on_changes_only", False)
        has_changes = any(e.get("new_artists", 0) > 0 for e in summary if e.get("status") == "ok")
        if not changes_only or has_changes:
            await _send_webhook(webhook_url, {"last_run": last_run, "summary": summary})
    logger.info("Scheduler: done")
    return {"last_run": last_run, "summary": summary}

async def _plex_sync_all_job() -> dict:
    """Re-sync every Digarr playlist that lives in Plex, updating only when more
    tracks have become available (i.e. Lidarr finished downloading some)."""
    logger.info("Plex sync: starting")
    config = load_config()
    if not (config.get("plex_url") and config.get("plex_token") and config.get("plex_library_section_id")):
        logger.info("Plex sync: Plex not configured, skipping")
        return {"synced": 0, "total": 0}

    playlists = get_playlists()
    candidates = [pl for pl in playlists if pl.get("plex_playlist_id")]
    plex_client = PlexClient(config["plex_url"], config["plex_token"], config["plex_library_section_id"])
    synced = 0

    for pl in candidates:
        try:
            full = get_playlist(pl["id"])
            if not full or not full.get("tracks"):
                continue
            matched_keys, unmatched, total = await plex_client.match_tracks(full["tracks"])
            current_matched = pl.get("plex_matched_count") or 0
            if len(matched_keys) <= current_matched:
                continue  # nothing new — skip
            # Delete old Plex playlist and recreate with updated track list
            try:
                await plex_client.delete_playlist(pl["plex_playlist_id"])
            except Exception:
                pass
            plex_name = _plex_playlist_name(pl["name"], config)
            new_id = await plex_client.create_playlist(plex_name, matched_keys)
            update_playlist_plex_result(pl["id"], new_id, len(matched_keys), total, unmatched,
                                        plex_playlist_name=plex_name)
            synced += 1
            logger.info("Plex sync: updated '%s' — %s/%s tracks", pl['name'], len(matched_keys), total)
        except Exception as exc:
            logger.error("Plex sync: error on '%s': %s", pl['name'], exc)

    logger.info("Plex sync: done — %s/%s updated", synced, len(candidates))
    return {"synced": synced, "total": len(candidates)}


async def _jellyfin_sync_all_job() -> dict:
    logger.info("Jellyfin sync: starting")
    config = load_config()
    if not (config.get("jellyfin_url") and config.get("jellyfin_api_key")):
        return {"synced": 0, "total": 0}
    playlists = get_playlists()
    candidates = [pl for pl in playlists if pl.get("jellyfin_playlist_id")]
    jf = JellyfinClient(config["jellyfin_url"], config["jellyfin_api_key"])
    synced = 0
    for pl in candidates:
        try:
            full = get_playlist(pl["id"])
            if not full or not full.get("tracks"):
                continue
            await _do_sync_jellyfin_playlist(full, jf, config)
            synced += 1
        except Exception as exc:
            logger.error("Jellyfin sync: error on '%s': %s", pl['name'], exc)
    logger.info("Jellyfin sync: done — %s/%s updated", synced, len(candidates))
    return {"synced": synced, "total": len(candidates)}


async def _navidrome_sync_all_job() -> dict:
    logger.info("Navidrome sync: starting")
    config = load_config()
    if not (config.get("navidrome_url") and config.get("navidrome_username")):
        return {"synced": 0, "total": 0}
    playlists = get_playlists()
    candidates = [pl for pl in playlists if pl.get("navidrome_playlist_id")]
    nd = NavidromeClient(config["navidrome_url"], config["navidrome_username"], config.get("navidrome_password", ""))
    synced = 0
    for pl in candidates:
        try:
            full = get_playlist(pl["id"])
            if not full or not full.get("tracks"):
                continue
            await _do_sync_navidrome_playlist(full, nd, config)
            synced += 1
        except Exception as exc:
            logger.error("Navidrome sync: error on '%s': %s", pl['name'], exc)
    logger.info("Navidrome sync: done — %s/%s updated", synced, len(candidates))
    return {"synced": synced, "total": len(candidates)}


@app.get("/api/scheduler/status")
def get_scheduler_status():
    config = load_config()
    job = scheduler.get_job("refresh_all")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    return {
        "interval_hours": int(config.get("refresh_interval_hours") or 0),
        "last_run": config.get("refresh_last_run"),
        "last_run_summary": config.get("refresh_last_run_summary"),
        "next_run": next_run,
    }

@app.post("/api/scheduler/run-now")
async def run_now():
    result = await _refresh_all_playlists_job()
    return {"status": "ok", **result}

# --- Background import jobs ---

class ImportJobRequest(BaseModel):
    artists: List[dict]
    tracks: List[dict] = []
    playlist_name: str = Field(default="", max_length=500)
    source_url: Optional[str] = Field(default=None, max_length=2000)
    source_type: str = "url"
    include_in_refresh: bool = True
    sync_targets: List[str] = []  # e.g. ["plex", "spotify"]; empty = all configured


def _new_job(playlist_name: str, total: int) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "status": "queued",
        "playlist_name": playlist_name,
        "current": 0,
        "total": total,
        "current_artist": None,
        "results": [],
        "playlist_id": None,
        "plex_result": None,
        "jellyfin_result": None,
        "navidrome_result": None,
        "deemix_result": None,
        "slskd_result": None,
        "created_at": datetime.utcnow().isoformat(),
        "completed_at": None,
        "error": None,
    }


def _prune_completed_jobs():
    done = sorted(
        [j for j in _jobs.values() if j["status"] in ("done", "error")],
        key=lambda j: j["created_at"],
    )
    while len(_jobs) > _MAX_COMPLETED_JOBS and done:
        job_id = done.pop(0)["id"]
        del _jobs[job_id]
        db_delete_import_job(job_id)


async def _run_import_job(job_id: str, req: ImportJobRequest, playlist_id: int):
    """Process artists in the background; playlist already exists in the DB."""
    job = _jobs[job_id]
    job["status"] = "running"

    config = load_config()
    blocklist = {normalize(a) for a in (config.get("artist_blocklist") or [])}

    targets = set(req.sync_targets) if req.sync_targets else {"plex", "spotify", "jellyfin", "navidrome", "deemix", "slskd"}

    # Plex push first — it only needs the track list, not Lidarr results,
    # so the playlist appears in Plex immediately while artists are being added.
    if ("plex" in targets and config.get("plex_url") and config.get("plex_token")
            and config.get("plex_library_section_id") and req.tracks):
        try:
            pc = PlexClient(config["plex_url"], config["plex_token"],
                            config["plex_library_section_id"])
            matched_keys, unmatched, total = await pc.match_tracks(req.tracks)
            if matched_keys:
                plex_name = _plex_playlist_name(job["playlist_name"], config)
                plex_id = await pc.create_playlist(plex_name, matched_keys)
                update_playlist_plex_result(playlist_id, plex_id,
                                           len(matched_keys), total, unmatched,
                                           plex_playlist_name=plex_name)
                job["plex_result"] = {"matched": len(matched_keys), "total": total}
        except Exception as exc:
            logger.error("Plex push failed for job %s: %s", job_id, exc)

    # Jellyfin push
    if ("jellyfin" in targets and config.get("jellyfin_url") and config.get("jellyfin_api_key")
            and req.tracks):
        try:
            jf = JellyfinClient(config["jellyfin_url"], config["jellyfin_api_key"])
            matched_ids, _, total = await jf.match_tracks(req.tracks)
            if matched_ids:
                jf_name = _jellyfin_playlist_name(job["playlist_name"], config)
                jf_id = await jf.create_playlist(jf_name, matched_ids)
                update_playlist_jellyfin_result(playlist_id, jf_id, len(matched_ids), total)
                job["jellyfin_result"] = {"matched": len(matched_ids), "total": total}
        except Exception as exc:
            logger.error("Jellyfin push failed for job %s: %s", job_id, exc)

    # Navidrome push
    if ("navidrome" in targets and config.get("navidrome_url") and config.get("navidrome_username")
            and req.tracks):
        try:
            nd = NavidromeClient(
                config["navidrome_url"], config["navidrome_username"],
                config.get("navidrome_password", ""),
            )
            matched_ids, _, total = await nd.match_tracks(req.tracks)
            if matched_ids:
                nd_name = _navidrome_playlist_name(job["playlist_name"], config)
                nd_id = await nd.create_playlist(nd_name, matched_ids)
                update_playlist_navidrome_result(playlist_id, nd_id, len(matched_ids), total)
                job["navidrome_result"] = {"matched": len(matched_ids), "total": total}
        except Exception as exc:
            logger.error("Navidrome push failed for job %s: %s", job_id, exc)

    # Spotify push — runs in parallel with Lidarr, same as Plex
    if "spotify" in targets and req.tracks:
        oauth_token = await get_oauth_token(config)
        user_id = config.get("spotify_user_id", "")
        if oauth_token and user_id:
            try:
                result = await push_to_spotify(
                    user_id=user_id,
                    name=job["playlist_name"],
                    tracks=req.tracks,
                    token=oauth_token,
                )
                update_playlist_spotify_result(
                    playlist_id, result["playlist_id"],
                    result["matched_count"], result["total_count"],
                )
                job["spotify_result"] = {"matched": result["matched_count"], "total": result["total_count"]}
            except Exception as exc:
                logger.error("Spotify push failed for job %s: %s", job_id, exc)

    # Deemix push — inline with import, async queue to Deezer via Deemix
    if "deemix" in targets and config.get("deemix_url") and req.tracks:
        try:
            dx = DeemixClient(config["deemix_url"])
            dx_result = await dx.queue_tracks(req.tracks)
            update_playlist_deemix_result(
                playlist_id, dx_result["queued"], len(req.tracks)
            )
            job["deemix_result"] = {"queued": dx_result["queued"], "total": len(req.tracks)}
        except Exception as exc:
            logger.error("Deemix push failed for job %s: %s", job_id, exc)

    # Build album hint and first-track maps from track list
    album_hint_map: dict = {}
    first_track_map: dict = {}
    for t in req.tracks:
        artist = t.get("artist", "")
        if not artist:
            continue
        if t.get("album") and t["album"] not in ("null", None) and artist not in album_hint_map:
            album_hint_map[artist] = t["album"]
        if t.get("title") and artist not in first_track_map:
            first_track_map[artist] = t["title"]

    lidarr_ok = bool(config.get("lidarr_url") and config.get("lidarr_api_key"))
    lidarr_client = make_lidarr_client(config) if lidarr_ok else None

    results = []
    artist_names = [a["name"] if isinstance(a, dict) else a for a in req.artists]

    for i, artist_name in enumerate(artist_names):
        job["current"] = i + 1
        job["current_artist"] = artist_name
        try:
            if not lidarr_ok:
                result = {"artist": artist_name, "status": "error",
                          "message": "Lidarr not configured", "album_monitored": None}
            elif normalize(artist_name) in blocklist:
                result = {"artist": artist_name, "status": "blocked",
                          "message": f"{artist_name} is on your blocklist", "album_monitored": None}
            else:
                album_hint = album_hint_map.get(artist_name)
                track_title = first_track_map.get(artist_name)
                resolved = artist_name
                if track_title and not album_hint:
                    mb = await mb_lookup_track(artist_name, track_title)
                    if mb.get("album"):
                        album_hint = mb["album"]
                        album_hint_map[artist_name] = album_hint  # persist for track back-fill
                    if mb.get("canonical_artist"):
                        resolved = mb["canonical_artist"]
                result = await asyncio.wait_for(
                    lidarr_client.add_artist(resolved, album_hint=album_hint),
                    timeout=45.0,
                )
                result = {**result, "artist": artist_name}
        except asyncio.TimeoutError:
            result = {"artist": artist_name, "status": "error",
                      "message": "Timed out", "album_monitored": None}
        except Exception as exc:
            result = {"artist": artist_name, "status": "error",
                      "message": str(exc), "album_monitored": None}
        results.append(result)

    job["results"] = results
    job["current_artist"] = None

    # Back-fill album names onto tracks that had null album, using MB-derived hints.
    # This enriches the stored track list so future Plex syncs can show the album column.
    enriched_tracks = [
        {**t, "album": album_hint_map[t.get("artist", "")]}
        if (not t.get("album") or t.get("album") in ("null", None))
           and t.get("artist", "") in album_hint_map
        else t
        for t in req.tracks
    ]
    if any(e.get("album") and t.get("album") != e.get("album")
           for t, e in zip(req.tracks, enriched_tracks)):
        update_playlist_tracks(playlist_id, enriched_tracks)

    # Update the playlist with final Lidarr results
    try:
        added = [r["artist"] for r in results if r.get("status") == "added"]
        update_playlist_import_results(playlist_id, added, results)

        # --- stats ---
        now_iso = datetime.utcnow().isoformat()
        db_set_stat_text_if_unset("first_import_at", now_iso)
        db_set_stat_text("last_import_at", now_iso)
        db_increment_stat("playlists_created_total")
        db_increment_stat("artists_parsed_total", len(req.artists))
        db_increment_stat("tracks_parsed_total", len(req.tracks))
        db_increment_stat(f"imports_by_source_{req.source_type}")
        db_increment_stat(f"imports_by_ai_{config.get('active_ai_provider', 'claude')}")
        if added:
            db_increment_stat("artists_added_to_lidarr_total", len(added))
        if job.get("plex_result"):
            db_increment_stat("plex_tracks_matched_total", job["plex_result"].get("matched", 0))
        if job.get("jellyfin_result"):
            db_increment_stat("jellyfin_tracks_matched_total", job["jellyfin_result"].get("matched", 0))
        if job.get("navidrome_result"):
            db_increment_stat("navidrome_tracks_matched_total", job["navidrome_result"].get("matched", 0))
        if job.get("spotify_result"):
            db_increment_stat("spotify_tracks_matched_total", job["spotify_result"].get("matched", 0))
        if job.get("deemix_result"):
            db_increment_stat("deemix_tracks_queued_total", job["deemix_result"].get("queued", 0))
        if job.get("slskd_result"):
            db_increment_stat("slskd_tracks_queued_total", job["slskd_result"].get("queued", 0))
            db_increment_stat("slskd_tracks_flagged_total", job["slskd_result"].get("flagged", 0))

        if config.get("playlist_export_path"):
            export_playlist_to_path(job["playlist_name"], req.tracks, config["playlist_export_path"])
    except Exception as exc:
        job["error"] = f"Failed to update playlist: {exc}"
        logger.error("Import job %s update error", job_id, exc_info=True)

    # Soulseek background phase — runs after Lidarr so we have artist info
    if "slskd" in targets and config.get("slskd_url") and config.get("slskd_api_key") and req.tracks:
        try:
            slskd = SlskdClient(
                config["slskd_url"],
                config["slskd_api_key"],
                confidence_threshold=int(config.get("slskd_confidence_threshold") or 85),
            )
            job["status"] = "running_slskd"
            sl_result = await slskd.queue_tracks(req.tracks)
            flagged = [r for r in sl_result["results"] if r["status"] == "flagged"]
            update_playlist_slskd_result(
                playlist_id,
                sl_result["queued"],
                sl_result["flagged"],
                len(req.tracks),
                flagged,
            )
            job["slskd_result"] = {
                "queued": sl_result["queued"],
                "flagged": sl_result["flagged"],
                "total": len(req.tracks),
            }
        except Exception as exc:
            logger.error("Soulseek phase failed for job %s: %s", job_id, exc)

    job["status"] = "done"
    job["completed_at"] = datetime.utcnow().isoformat()
    db_save_import_job(job)
    db_prune_import_jobs()
    _prune_completed_jobs()


@app.post("/api/import/start")
async def start_import_job(req: ImportJobRequest):
    name = req.playlist_name.strip() or f"Import {datetime.utcnow().strftime('%b %d')}"
    artist_names = [a["name"] if isinstance(a, dict) else a for a in req.artists]

    # Create the playlist immediately so it appears in History right away
    playlist_id = save_playlist(
        name=name,
        source_url=req.source_url,
        source_type=req.source_type,
        artists=artist_names,
        tracks=req.tracks,
        artists_added=[],
        lidarr_results=[],
    )
    if req.source_url and not req.include_in_refresh:
        cfg = load_config()
        excl = set(cfg.get("refresh_excluded_playlist_ids") or [])
        excl.add(playlist_id)
        save_config({**cfg, "refresh_excluded_playlist_ids": list(excl)})

    job = _new_job(name, len(req.artists))
    job["playlist_id"] = playlist_id
    _jobs[job["id"]] = job
    db_save_import_job(job)
    asyncio.create_task(_run_import_job(job["id"], req, playlist_id))
    return {"job_id": job["id"], "playlist_id": playlist_id}


@app.get("/api/import/jobs")
def list_import_jobs():
    jobs = sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)
    return {"jobs": jobs}


@app.get("/api/import/jobs/{job_id}")
def get_import_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.delete("/api/import/jobs/{job_id}")
def dismiss_import_job(job_id: str):
    _jobs.pop(job_id, None)
    db_delete_import_job(job_id)
    return {"ok": True}


# --- Parse ---

class ParseRequest(BaseModel):
    input_type: Literal["url", "text", "file"]
    content: str = Field(default="", max_length=200_000)
    playlist_name: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_url_scheme(self):
        if self.input_type == "url" and self.content:
            if not self.content.startswith(("http://", "https://")):
                raise ValueError("URL must start with http:// or https://")
        return self

class ParseResult(BaseModel):
    artists: List[dict]
    tracks: List[dict]
    raw_source: str
    playlist_name: Optional[str] = None

@app.post("/api/parse")
@limiter.limit("20/minute")
async def parse_input(request: Request, req: ParseRequest):
    config = load_config()

    # Spotify playlist — bypass AI entirely, use API directly
    if req.input_type == "url":
        playlist_id = extract_playlist_id(req.content)
        if playlist_id:
            # Try OAuth token first; fall back to client credentials for public playlists
            token = await get_oauth_token(config)
            if not token:
                client_id = config.get("spotify_client_id", "")
                client_secret = config.get("spotify_client_secret", "")
                if not client_id or not client_secret:
                    raise HTTPException(
                        status_code=400,
                        detail="Spotify not configured. Connect your account in Settings → Spotify, or add client credentials for public playlists.",
                    )
                # Editorial playlists require user auth
                if playlist_id.startswith("37i9dQZF1E"):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "This is a Spotify-curated playlist (Discover Weekly, Daily Mix, etc.). "
                            "Connect your Spotify account in Settings → Spotify to import these."
                        ),
                    )
                try:
                    token = await get_access_token(client_id, client_secret)
                except httpx.HTTPStatusError as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Spotify authentication failed ({e.response.status_code}). Check your client ID and secret in Settings.",
                    )
            try:
                data = await fetch_playlist(playlist_id, token)
            except httpx.HTTPStatusError as e:
                try:
                    spotify_msg = e.response.json().get("error", {}).get("message", "")
                except Exception:
                    spotify_msg = e.response.text[:200]
                detail = f"Spotify API error ({e.response.status_code}) fetching playlist."
                if spotify_msg:
                    detail += f" Spotify says: {spotify_msg}"
                if e.response.status_code == 404:
                    detail += (
                        " The playlist may be private, or it may be a Spotify-curated editorial playlist"
                        " (e.g. 'Today's Top Hits') which the API blocks for third-party apps."
                        " Try a user-created public playlist instead."
                    )
                raise HTTPException(status_code=400, detail=detail)
            return {
                "artists": data["artists"],
                "tracks": data["tracks"],
                "raw_source": req.content,
                "playlist_name": req.playlist_name or data["name"],
            }

    # Fetch content if URL
    content = req.content
    if req.input_type == "url":
        content = await fetch_url_content(req.content)

    # Parse M3U directly if content looks like M3U (URL that fetched M3U, or pasted M3U text)
    if content.strip().startswith("#EXTM3U"):
        tracks = parse_m3u_content(content)
        artists = list({t["artist"]: {"name": t["artist"]} for t in tracks if t.get("artist")}.values())
        detected_type = "m3u_url" if req.input_type == "url" else "file"
        return {"artists": artists, "tracks": tracks, "raw_source": content[:500], "playlist_name": req.playlist_name, "detected_source_type": detected_type}

    # Use configured AI provider for everything else
    try:
        ai = make_ai_provider(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result = await ai.extract_artists_and_tracks(content)
    # Deduplicate artists by name (AI may return duplicates despite instructions)
    result["artists"] = deduplicate_artists(result.get("artists", []))
    result["playlist_name"] = req.playlist_name
    usage = result.get("usage")
    if usage:
        db_increment_stat("tokens_input_total", usage.get("input_tokens", 0))
        db_increment_stat("tokens_output_total", usage.get("output_tokens", 0))
        db_increment_stat(f"tokens_input_{usage.get('provider', 'unknown')}", usage.get("input_tokens", 0))
        db_increment_stat(f"tokens_output_{usage.get('provider', 'unknown')}", usage.get("output_tokens", 0))
    return result

@app.post("/api/parse/upload")
@limiter.limit("20/minute")
async def parse_upload(request: Request, file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode("utf-8", errors="ignore")
    tracks = parse_m3u_content(text)
    artists = list({t["artist"]: {"name": t["artist"]} for t in tracks if t.get("artist")}.values())
    return {"artists": artists, "tracks": tracks, "raw_source": text[:500], "playlist_name": file.filename}

# --- Lidarr ---

class AddArtistsRequest(BaseModel):
    artists: List[str]
    playlist_id: Optional[int] = None

@app.post("/api/lidarr/add")
async def add_artists(req: AddArtistsRequest):
    config = load_config()
    if not config.get("lidarr_url") or not config.get("lidarr_api_key"):
        raise HTTPException(status_code=400, detail="Lidarr not configured")

    client = make_lidarr_client(config)

    config_errors = await client.validate_config()
    if config_errors:
        raise HTTPException(status_code=400, detail=" | ".join(config_errors))

    # Deduplicate by name before hitting Lidarr
    seen = set()
    unique_artists = []
    for name in req.artists:
        if name and name not in seen:
            seen.add(name)
            unique_artists.append(name)

    # Fetch library once so each add_artist doesn't make its own full-library call
    library = await client.get_all_artists()

    semaphore = asyncio.Semaphore(5)

    async def add_one(name):
        async with semaphore:
            return await client.add_artist(name, _library=library)

    raw = await asyncio.gather(*[add_one(n) for n in unique_artists], return_exceptions=True)
    results = [
        r if not isinstance(r, BaseException)
        else {"artist": unique_artists[i], "status": "error", "message": str(r)}
        for i, r in enumerate(raw)
    ]

    return {"results": results}

class CheckArtistsRequest(BaseModel):
    artists: List[str]

@app.post("/api/lidarr/check-artists")
async def check_artists_in_library(req: CheckArtistsRequest):
    config = load_config()
    if not config.get("lidarr_url") or not config.get("lidarr_api_key"):
        # Lidarr not configured — treat none as existing so all are pre-selected
        return {"results": {name: False for name in req.artists}}
    client = make_lidarr_client(config)
    results = await client.check_artists_in_library(req.artists)
    return {"results": results}

class AddSingleArtistRequest(BaseModel):
    artist: str
    album_hint: Optional[str] = None
    track_title: Optional[str] = None

@app.post("/api/lidarr/add-single")
async def add_single_artist(req: AddSingleArtistRequest):
    config = load_config()
    if not config.get("lidarr_url") or not config.get("lidarr_api_key"):
        raise HTTPException(status_code=400, detail="Lidarr not configured")

    client = make_lidarr_client(config)
    config_errors = await client.validate_config()
    if config_errors:
        raise HTTPException(status_code=400, detail=" | ".join(config_errors))

    # Blocklist check — skip before any external calls
    blocklist = {normalize(a) for a in (config.get("artist_blocklist") or [])}
    if normalize(req.artist) in blocklist:
        return {
            "artist": req.artist,
            "status": "blocked",
            "message": f"{req.artist} is on your blocklist",
            "album_monitored": None,
        }

    artist_name = req.artist
    album_hint = req.album_hint or None

    # Enrich via MusicBrainz when we have a track title but no album hint.
    # This resolves the album for existing Lidarr artists so the specific
    # album gets monitored, not just the artist.
    if req.track_title and not album_hint:
        mb = await mb_lookup_track(req.artist, req.track_title)
        if mb.get("album"):
            album_hint = mb["album"]
        # Use canonical artist name if MB found one (helps with e.g. "Verve" → "The Verve"),
        # but keep the original name in the result so the frontend can match it back.
        if mb.get("canonical_artist"):
            artist_name = mb["canonical_artist"]

    try:
        result = await asyncio.wait_for(
            client.add_artist(artist_name, album_hint=album_hint),
            timeout=45.0,
        )
        # Always return the original requested artist name so the frontend result
        # mapping works correctly regardless of any canonical name substitution.
        result = {**result, "artist": req.artist}
    except asyncio.TimeoutError:
        result = {
            "artist": req.artist,
            "status": "error",
            "message": f"Timed out adding {req.artist}",
            "album_monitored": None,
        }
    return result

class TrackStatusRequest(BaseModel):
    tracks: List[dict]  # each: {artist, title, album}

@app.post("/api/lidarr/trackstatus")
async def get_track_status(req: TrackStatusRequest):
    config = load_config()
    if not config.get("lidarr_url") or not config.get("lidarr_api_key"):
        raise HTTPException(status_code=400, detail="Lidarr not configured")
    client = make_lidarr_client(config)
    statuses = await client.get_track_statuses(req.tracks)
    return {"tracks": statuses}

@app.get("/api/lidarr/library")
async def get_library():
    config = load_config()
    if not config.get("lidarr_url") or not config.get("lidarr_api_key"):
        raise HTTPException(status_code=400, detail="Lidarr not configured")
    client = make_lidarr_client(config)
    artists = await client.get_all_artists()
    return {"artists": artists}

@app.get("/api/lidarr/profiles")
async def get_profiles():
    config = load_config()
    if not config.get("lidarr_url") or not config.get("lidarr_api_key"):
        raise HTTPException(status_code=400, detail="Lidarr not configured")
    client = make_lidarr_client(config)
    quality = await client.get_quality_profiles()
    metadata = await client.get_metadata_profiles()
    root_folders = await client.get_root_folders()
    return {"quality_profiles": quality, "metadata_profiles": metadata, "root_folders": root_folders}

@app.get("/api/stats", include_in_schema=False)
def get_stats():
    """Hidden endpoint — not linked in the UI. Tracks cumulative usage stats for future use."""
    return db_get_all_stats()


@app.get("/api/lidarr/wanted")
async def get_wanted_missing():
    config = load_config()
    if not config.get("lidarr_url") or not config.get("lidarr_api_key"):
        raise HTTPException(status_code=400, detail="Lidarr not configured")

    # Build the normalised set of every artist name across all Digarr playlists.
    # Uses both `artists` (full list) and `artists_added` so we don't miss artists
    # that were already in the Lidarr library at import time.
    raw_names = get_all_playlist_artist_names()
    digarr_artists = {normalize(n) for n in raw_names}

    client = make_lidarr_client(config)
    try:
        data = await client.get_wanted_missing(page_size=200)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Lidarr timed out. It may be busy or unreachable.")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Lidarr returned {e.response.status_code}.")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Lidarr: {e}")

    records = data.get("records", [])

    albums = []
    for r in records:
        artist_name = r.get("artist", {}).get("artistName", "")
        if normalize(artist_name) not in digarr_artists:
            continue
        albums.append({
            "artist": artist_name,
            "title": r.get("title", ""),
            "release_date": (r.get("releaseDate") or "")[:10] or None,
        })

    return {
        "total": len(albums),
        "albums": albums,
        "lidarr_total": data.get("totalRecords", len(records)),
        "digarr_artist_count": len(digarr_artists),
    }

# --- Playlist file export ---

def _safe_filename(name: str) -> str:
    """Convert a playlist name to a safe filename (no special chars, spaces → underscores)."""
    name = re.sub(r'[^\w\s-]', '', name).strip()
    return re.sub(r'\s+', '_', name) or 'playlist'

def export_playlist_to_path(name: str, tracks: list, export_path: str) -> None:
    """
    Write an M3U file for the playlist to export_path.
    Creates the directory if it doesn't exist. Silent on failure — export
    is best-effort and should never block the main import/refresh flow.
    """
    if not export_path:
        return
    try:
        os.makedirs(export_path, exist_ok=True)
        filename = _safe_filename(name) + '.m3u'
        filepath = os.path.join(export_path, filename)
        lines = ['#EXTM3U', f'#PLAYLIST:{name}']
        for track in tracks:
            artist = track.get('artist') or 'Unknown'
            title = track.get('title') or 'Unknown'
            lines.append(f'#EXTINF:-1,{artist} - {title}')
            lines.append(f'# {artist} - {title}')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning('Failed to export playlist to %s: %s', export_path, exc)

# --- Playlists ---

class SavePlaylistRequest(BaseModel):
    name: str
    source_url: Optional[str] = None
    source_type: str
    artists: List[str]
    tracks: List[dict]
    artists_added: List[str]
    lidarr_results: List[dict] = []

@app.post("/api/playlists")
def create_playlist(req: SavePlaylistRequest):
    playlist_id = save_playlist(
        name=req.name,
        source_url=req.source_url,
        source_type=req.source_type,
        artists=req.artists,
        tracks=req.tracks,
        artists_added=req.artists_added,
        lidarr_results=req.lidarr_results,
    )
    config = load_config()
    if config.get("playlist_export_path"):
        export_playlist_to_path(req.name, req.tracks, config["playlist_export_path"])
    return {"id": playlist_id}

@app.get("/api/playlists")
def list_playlists():
    return {"playlists": get_playlists()}

@app.get("/api/playlists/check-source")
def check_source_url(url: str):
    matches = [p for p in get_playlists() if p.get("source_url") == url]
    return {"matches": [{"id": p["id"], "name": p["name"], "created_at": p["created_at"]} for p in matches]}

@app.get("/api/playlists/{playlist_id}")
def get_playlist_detail(playlist_id: int):
    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return pl

@app.delete("/api/playlists/{playlist_id}")
async def delete_playlist_route(playlist_id: int):
    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")

    config = load_config()
    if pl.get("plex_playlist_id") and config.get("plex_delete_on_remove") and config.get("plex_url") and config.get("plex_token"):
        try:
            await PlexClient(config["plex_url"], config["plex_token"], config.get("plex_library_section_id", "")).delete_playlist(pl["plex_playlist_id"])
        except Exception:
            pass
    if pl.get("jellyfin_playlist_id") and config.get("jellyfin_delete_on_remove") and config.get("jellyfin_url") and config.get("jellyfin_api_key"):
        try:
            await JellyfinClient(config["jellyfin_url"], config["jellyfin_api_key"]).delete_playlist(pl["jellyfin_playlist_id"])
        except Exception:
            pass
    if pl.get("navidrome_playlist_id") and config.get("navidrome_delete_on_remove") and config.get("navidrome_url") and config.get("navidrome_username"):
        try:
            await NavidromeClient(config["navidrome_url"], config["navidrome_username"], config.get("navidrome_password", "")).delete_playlist(pl["navidrome_playlist_id"])
        except Exception:
            pass

    delete_playlist(playlist_id)
    # Remove import jobs for this playlist from memory and the DB
    to_remove = [jid for jid, j in _jobs.items() if j.get("playlist_id") == playlist_id]
    for jid in to_remove:
        del _jobs[jid]
    db_delete_import_jobs_for_playlist(playlist_id)
    return {"ok": True}

class SetRefreshRequest(BaseModel):
    excluded: bool

@app.post("/api/playlists/{playlist_id}/set-refresh")
def set_playlist_refresh(playlist_id: int, req: SetRefreshRequest):
    config = load_config()
    excluded = set(config.get("refresh_excluded_playlist_ids") or [])
    if req.excluded:
        excluded.add(playlist_id)
    else:
        excluded.discard(playlist_id)
    config["refresh_excluded_playlist_ids"] = list(excluded)
    save_config(config)
    return {"ok": True}

class SetMergeTracksRequest(BaseModel):
    merge_tracks: bool | None  # None = inherit global setting

@app.post("/api/playlists/{playlist_id}/set-merge-tracks")
def set_playlist_merge_tracks_route(playlist_id: int, req: SetMergeTracksRequest):
    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    value = int(req.merge_tracks) if req.merge_tracks is not None else None
    set_playlist_merge_tracks(playlist_id, value)
    return {"ok": True}

class RenamePlaylistRequest(BaseModel):
    name: str

@app.put("/api/playlists/{playlist_id}/name")
async def rename_playlist_route(playlist_id: int, req: RenamePlaylistRequest):
    new_name = req.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")

    # If playlist is in Plex, rename there first — if it fails we abort before touching SQLite
    if pl.get("plex_playlist_id"):
        config = load_config()
        if not config.get("plex_url") or not config.get("plex_token"):
            raise HTTPException(status_code=400, detail="Plex is configured for this playlist but credentials are missing from Settings.")
        plex_name = _plex_playlist_name(new_name, config)
        try:
            plex_client = PlexClient(config["plex_url"], config["plex_token"], config.get("plex_library_section_id", ""))
            await plex_client.rename_playlist(pl["plex_playlist_id"], plex_name)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Plex rename failed — SQLite not updated: {e}")
    else:
        config = load_config()
        plex_name = new_name
    plex_playlist_name = plex_name if pl.get("plex_playlist_id") else None

    rename_playlist(playlist_id, new_name, plex_playlist_name=plex_playlist_name if pl.get("plex_playlist_id") else None)
    return {"ok": True, "name": new_name}

async def _do_refresh_playlist(playlist_id: int) -> dict:
    """Shared refresh logic used by the API endpoint and the scheduler."""
    config = load_config()
    pl = get_playlist(playlist_id)
    if not pl:
        raise ValueError(f"Playlist {playlist_id} not found")

    source_url = pl.get("source_url")
    source_type = pl.get("source_type")

    if not source_url or source_type not in ("url", "m3u_url", "listenbrainz", "similar", "discogs", "spotify"):
        raise ValueError("No refreshable source URL")

    new_artists_dicts = []
    new_tracks = []

    if source_type == "listenbrainz":
        username = config.get("listenbrainz_username", "").strip()
        if not username:
            raise ValueError("ListenBrainz username not configured in Settings.")
        parts = source_url.split(":")  # e.g. "listenbrainz:weekly_jams"
        playlist_type = parts[1] if len(parts) > 1 else "weekly_jams"
        data = await lb_recommendation(username, playlist_type)
        new_artists_dicts = data["artists"]
        new_tracks = data["tracks"]

    elif source_type == "similar":
        api_key = config.get("lastfm_api_key", "").strip()
        if not api_key:
            raise ValueError("Last.fm API key required for Similar to Library. Add it in Settings.")
        if not config.get("lidarr_url") or not config.get("lidarr_api_key"):
            raise ValueError("Lidarr not configured.")
        all_lidarr = await make_lidarr_client(config).get_all_artists()
        artist_names = [a.get("artistName", "") for a in all_lidarr if a.get("artistName")]
        data = await _compute_similar_to_library(api_key, artist_names)
        new_artists_dicts = data["artists"]
        new_tracks = data["tracks"]

    elif source_type == "discogs":
        token = config.get("discogs_token", "").strip()
        username = config.get("discogs_username", "").strip()
        if not token or not username:
            raise ValueError("Discogs username and token required. Add them in Settings.")
        data = await discogs_get_wantlist(token, username)
        new_artists_dicts = data["artists"]
        new_tracks = data["tracks"]

    elif source_type == "spotify":
        # source_url is "spotify:{playlist_id}" (from the Spotify tab import)
        sp_playlist_id = source_url.split(":", 1)[1] if ":" in source_url else source_url
        token = await get_oauth_token(config)
        if not token:
            client_id = config.get("spotify_client_id", "")
            client_secret = config.get("spotify_client_secret", "")
            if not client_id or not client_secret:
                raise ValueError("Spotify not configured in Settings.")
            token = await get_access_token(client_id, client_secret)
        data = await fetch_playlist(sp_playlist_id, token)
        new_artists_dicts = data["artists"]
        new_tracks = data["tracks"]

    elif source_type in ("url", "m3u_url"):
        playlist_id_spot = extract_playlist_id(source_url) if source_type == "url" else None
        if playlist_id_spot:
            token = await get_oauth_token(config)
            if not token:
                client_id = config.get("spotify_client_id", "")
                client_secret = config.get("spotify_client_secret", "")
                if not client_id or not client_secret:
                    raise ValueError("Spotify not configured in Settings.")
                if playlist_id_spot.startswith("37i9dQZF1E"):
                    raise ValueError("Spotify account not connected — editorial playlists require OAuth. Go to Settings → Spotify.")
                token = await get_access_token(client_id, client_secret)
            data = await fetch_playlist(playlist_id_spot, token)
            new_artists_dicts = data["artists"]
            new_tracks = data["tracks"]
        else:
            content = await fetch_url_content(source_url)
            if source_type == "m3u_url" or content.strip().startswith("#EXTM3U"):
                new_tracks = parse_m3u_content(content)
                new_artists_dicts = list({t["artist"]: {"name": t["artist"]} for t in new_tracks if t.get("artist")}.values())
            else:
                ai = make_ai_provider(config)
                result = await ai.extract_artists_and_tracks(content)
                new_artists_dicts = deduplicate_artists(result.get("artists", []))
                new_tracks = result.get("tracks", [])
                usage = result.get("usage")
                if usage:
                    db_increment_stat("tokens_input_total", usage.get("input_tokens", 0))
                    db_increment_stat("tokens_output_total", usage.get("output_tokens", 0))
                    db_increment_stat(f"tokens_input_{usage.get('provider', 'unknown')}", usage.get("input_tokens", 0))
                    db_increment_stat(f"tokens_output_{usage.get('provider', 'unknown')}", usage.get("output_tokens", 0))

    blocklist = {normalize(a) for a in (config.get("artist_blocklist") or [])}
    existing_lower = {a.lower() for a in (pl.get("artists") or [])}
    net_new_names = []
    for a in new_artists_dicts:
        name = a["name"] if isinstance(a, dict) else a
        if name and name.lower() not in existing_lower and normalize(name) not in blocklist:
            net_new_names.append(name)

    lidarr_results = []
    if net_new_names and config.get("lidarr_url") and config.get("lidarr_api_key"):
        lidarr = make_lidarr_client(config)
        library = await lidarr.get_all_artists()
        raw = await asyncio.gather(
            *[lidarr.add_artist(name, _library=library) for name in net_new_names],
            return_exceptions=True,
        )
        lidarr_results = [
            r if not isinstance(r, BaseException)
            else {"artist": net_new_names[i], "status": "error", "message": str(r)}
            for i, r in enumerate(raw)
        ]

    # Build fresh artist list from the new source (overwrite, not append)
    seen_lower = set()
    existing_names = []
    for a in new_artists_dicts:
        name = a["name"] if isinstance(a, dict) else a
        if name and name.lower() not in seen_lower:
            existing_names.append(name)
            seen_lower.add(name.lower())

    newly_added = [r["artist"] for r in lidarr_results if r.get("status") in ("added", "exists")]
    all_artists_added = list(set((pl.get("artists_added") or []) + newly_added))

    # Per-playlist override takes precedence; fall back to global setting.
    pl_merge = pl.get("merge_tracks")
    use_merge = pl_merge if pl_merge is not None else config.get("refresh_merge_tracks", False)
    if use_merge:
        existing_tracks = list(pl.get("tracks") or [])
        existing_track_keys = {
            (t.get("artist", "").lower(), t.get("title", "").lower())
            for t in existing_tracks
        }
        for t in new_tracks:
            k = (t.get("artist", "").lower(), t.get("title", "").lower())
            if k not in existing_track_keys:
                existing_tracks.append(t)
                existing_track_keys.add(k)
        tracks_to_save = existing_tracks
    else:
        tracks_to_save = new_tracks

    old_track_keys = {
        (t.get("artist", "").lower(), t.get("title", "").lower())
        for t in (pl.get("tracks") or [])
    }
    new_track_keys = {
        (t.get("artist", "").lower(), t.get("title", "").lower())
        for t in tracks_to_save
    }
    tracks_changed = old_track_keys != new_track_keys

    update_playlist(playlist_id, existing_names, tracks_to_save, all_artists_added)
    touch_playlist_refreshed(playlist_id)
    config = load_config()
    if config.get("playlist_export_path"):
        export_playlist_to_path(pl["name"], tracks_to_save, config["playlist_export_path"])

    # Auto-sync Plex if track list changed, or if new tracks have been matched in the library
    plex_sync_result = None
    if pl.get("plex_playlist_id") and config.get("plex_url") and config.get("plex_token") and config.get("plex_library_section_id"):
        try:
            plex_client = PlexClient(config["plex_url"], config["plex_token"], config["plex_library_section_id"])
            matched_keys, unmatched, total = await plex_client.match_tracks(tracks_to_save)
            current_matched = pl.get("plex_matched_count") or 0
            if tracks_changed or len(matched_keys) > current_matched:
                old_plex_id = pl.get("plex_playlist_id")
                if old_plex_id:
                    try:
                        await plex_client.delete_playlist(old_plex_id)
                    except Exception:
                        pass
                plex_name = _plex_playlist_name(pl["name"], config)
                new_plex_id = await plex_client.create_playlist(plex_name, matched_keys)
                update_playlist_plex_result(playlist_id, new_plex_id, len(matched_keys), total, unmatched, plex_playlist_name=plex_name)
                plex_sync_result = {
                    "matched": len(matched_keys),
                    "total": total,
                    "plex_playlist_id": new_plex_id,
                    "unmatched": unmatched,
                }
        except Exception as e:
            logger.error("Auto Plex sync failed for playlist %s: %s", playlist_id, e)

    # Auto-sync Spotify if this playlist was previously pushed there
    spotify_sync_result = None
    if pl.get("spotify_playlist_id"):
        oauth_token = await get_oauth_token(config)
        user_id = config.get("spotify_user_id", "")
        if oauth_token and user_id:
            try:
                result = await push_to_spotify(
                    user_id=user_id,
                    name=pl["name"],
                    tracks=tracks_to_save,
                    token=oauth_token,
                    existing_playlist_id=pl["spotify_playlist_id"],
                )
                update_playlist_spotify_result(
                    playlist_id, result["playlist_id"],
                    result["matched_count"], result["total_count"],
                )
                spotify_sync_result = {
                    "matched": result["matched_count"],
                    "total": result["total_count"],
                    "playlist_id": result["playlist_id"],
                }
            except Exception as e:
                logger.error("Auto Spotify sync failed for playlist %s: %s", playlist_id, e)

    # Auto-sync Jellyfin if this playlist was previously pushed there
    jellyfin_sync_result = None
    if pl.get("jellyfin_playlist_id") and config.get("jellyfin_url") and config.get("jellyfin_api_key"):
        try:
            jf = JellyfinClient(config["jellyfin_url"], config["jellyfin_api_key"])
            cache_matched, live_tracks = [], []
            for t in tracks_to_save:
                cached = db_lookup_track_cache(t.get("artist", ""), t.get("title", ""), "jellyfin")
                if cached:
                    cache_matched.append(cached)
                else:
                    live_tracks.append(t)
            live_matched, _, total = await jf.match_tracks(live_tracks)
            total = len(tracks_to_save)
            matched_ids = cache_matched + live_matched
            current_matched = pl.get("jellyfin_matched_count") or 0
            if tracks_changed or len(matched_ids) > current_matched:
                await jf.update_playlist(pl["jellyfin_playlist_id"], matched_ids)
                update_playlist_jellyfin_result(playlist_id, pl["jellyfin_playlist_id"], len(matched_ids), total)
                jellyfin_sync_result = {"matched": len(matched_ids), "total": total}
        except Exception as e:
            logger.error("Auto Jellyfin sync failed for playlist %s: %s", playlist_id, e)

    # Auto-sync Navidrome if this playlist was previously pushed there
    navidrome_sync_result = None
    if (pl.get("navidrome_playlist_id") and config.get("navidrome_url")
            and config.get("navidrome_username")):
        try:
            nd = NavidromeClient(
                config["navidrome_url"], config["navidrome_username"],
                config.get("navidrome_password", ""),
            )
            cache_matched, live_tracks = [], []
            for t in tracks_to_save:
                cached = db_lookup_track_cache(t.get("artist", ""), t.get("title", ""), "navidrome")
                if cached:
                    cache_matched.append(cached)
                else:
                    live_tracks.append(t)
            live_matched, _, _ = await nd.match_tracks(live_tracks)
            total = len(tracks_to_save)
            matched_ids = cache_matched + live_matched
            current_matched = pl.get("navidrome_matched_count") or 0
            if tracks_changed or len(matched_ids) > current_matched:
                nd_name = _navidrome_playlist_name(pl["name"], config)
                await nd.update_playlist(pl["navidrome_playlist_id"], nd_name, matched_ids)
                update_playlist_navidrome_result(playlist_id, pl["navidrome_playlist_id"], len(matched_ids), total)
                navidrome_sync_result = {"matched": len(matched_ids), "total": total}
        except Exception as e:
            logger.error("Auto Navidrome sync failed for playlist %s: %s", playlist_id, e)

    return {
        "new_artists": net_new_names,
        "new_artists_added": len(newly_added),
        "lidarr_results": lidarr_results,
        "total_tracks": len(tracks_to_save),
        "total_artists": len(existing_names),
        "plex_sync": plex_sync_result,
        "spotify_sync": spotify_sync_result,
        "jellyfin_sync": jellyfin_sync_result,
        "navidrome_sync": navidrome_sync_result,
    }

@app.post("/api/playlists/{playlist_id}/refresh")
async def refresh_playlist(playlist_id: int):
    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if not pl.get("source_url") or pl.get("source_type") not in ("url", "m3u_url", "listenbrainz", "similar", "discogs", "spotify"):
        raise HTTPException(status_code=400, detail="This playlist has no refreshable source URL.")
    try:
        return await _do_refresh_playlist(playlist_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/playlists/{playlist_id}/m3u")
def download_m3u(playlist_id: int):
    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")

    lines = ["#EXTM3U", f"#PLAYLIST:{pl['name']}"]
    for track in pl.get("tracks", []):
        artist = track.get("artist", "Unknown")
        title = track.get("title", "Unknown")
        lines.append(f"#EXTINF:-1,{artist} - {title}")
        lines.append(f"# {artist} - {title}")

    content = "\n".join(lines)
    filename = pl["name"].replace(" ", "_") + ".m3u"

    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type="audio/x-mpegurl",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/playlists/{playlist_id}/jspf")
def download_jspf(playlist_id: int):
    import json as _json
    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")

    tracks = [
        {
            "creator": t.get("artist", ""),
            "title": t.get("title", ""),
            **({"album": t["album"]} if t.get("album") and t["album"] != "null" else {}),
        }
        for t in pl.get("tracks", [])
    ]
    payload = _json.dumps({"playlist": {"title": pl["name"], "track": tracks}}, indent=2)
    filename = pl["name"].replace(" ", "_") + ".jspf"
    return StreamingResponse(
        io.BytesIO(payload.encode()),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# --- Plex ---

class PlexPlaylistRequest(BaseModel):
    playlist_name: str
    tracks: List[dict]
    digarr_playlist_id: Optional[int] = None

@app.get("/api/plex/sections")
async def get_plex_sections():
    """Return all Plex library sections so the user can pick the right music library ID."""
    config = load_config()
    if not config.get("plex_url") or not config.get("plex_token"):
        raise HTTPException(status_code=400, detail="Plex URL and token must be saved first")
    client = PlexClient(config["plex_url"], config["plex_token"], "")
    try:
        sections = await client.get_sections()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not connect to Plex: {exc}")
    return {"sections": sections}


@app.get("/api/plex/test")
async def test_plex(query: str = ""):
    """
    Diagnostic endpoint. Returns:
      - total track count in the music section (confirms connection + section ID)
      - raw search results for `query` via /search?query= (up to 5)
      - raw filter results for `query` via /all?type=10&title= (up to 5)
    """
    config = load_config()
    if not config.get("plex_url") or not config.get("plex_token"):
        raise HTTPException(status_code=400, detail="Plex not configured")
    if not config.get("plex_library_section_id"):
        raise HTTPException(status_code=400, detail="Plex library section ID not set")

    base = config["plex_url"].rstrip("/")
    token = config["plex_token"]
    section = config["plex_library_section_id"]
    headers = {"Accept": "application/json"}
    params_base = {"X-Plex-Token": token}

    out = {}
    async with httpx.AsyncClient(timeout=15) as client:
        # 1. Count tracks in section
        try:
            r = await client.get(
                f"{base}/library/sections/{section}/all",
                params={**params_base, "type": 10, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 1},
                headers=headers,
            )
            mc = r.json().get("MediaContainer", {})
            out["section_track_total"] = mc.get("totalSize", mc.get("size", "?"))
            out["section_status"] = r.status_code
        except Exception as e:
            out["section_error"] = str(e)

        if query:
            # 2. /search?query= endpoint
            try:
                r = await client.get(
                    f"{base}/library/sections/{section}/search",
                    params={**params_base, "type": 10, "query": query},
                    headers=headers,
                )
                tracks = r.json().get("MediaContainer", {}).get("Metadata", [])
                out["search_endpoint_status"] = r.status_code
                out["search_endpoint_results"] = [
                    {"title": t.get("title"), "artist": t.get("grandparentTitle"), "album": t.get("parentTitle")}
                    for t in tracks[:5]
                ]
                out["search_endpoint_count"] = len(tracks)
            except Exception as e:
                out["search_endpoint_error"] = str(e)

            # 3. /all?type=10&title= filter
            try:
                r = await client.get(
                    f"{base}/library/sections/{section}/all",
                    params={**params_base, "type": 10, "title": query},
                    headers=headers,
                )
                tracks = r.json().get("MediaContainer", {}).get("Metadata", [])
                out["all_title_filter_status"] = r.status_code
                out["all_title_filter_results"] = [
                    {"title": t.get("title"), "artist": t.get("grandparentTitle"), "album": t.get("parentTitle")}
                    for t in tracks[:5]
                ]
                out["all_title_filter_count"] = len(tracks)
            except Exception as e:
                out["all_title_filter_error"] = str(e)

            # 4. /hubs/search global
            try:
                r = await client.get(
                    f"{base}/hubs/search",
                    params={**params_base, "query": query, "sectionId": section, "limit": 5},
                    headers=headers,
                )
                hubs = r.json().get("MediaContainer", {}).get("Hub", [])
                track_hub = next((h for h in hubs if h.get("type") == "track"), None)
                out["hubs_search_status"] = r.status_code
                if track_hub:
                    out["hubs_search_results"] = [
                        {"title": t.get("title"), "artist": t.get("grandparentTitle"), "album": t.get("parentTitle")}
                        for t in (track_hub.get("Metadata") or [])[:5]
                    ]
                    out["hubs_search_count"] = track_hub.get("size", 0)
                else:
                    out["hubs_search_results"] = []
                    out["hubs_search_count"] = 0
            except Exception as e:
                out["hubs_search_error"] = str(e)

    return out


@app.post("/api/plex/playlist")
async def push_to_plex(req: PlexPlaylistRequest):
    config = load_config()
    if not config.get("plex_url") or not config.get("plex_token"):
        raise HTTPException(status_code=400, detail="Plex not configured. Add your Plex URL and token in Settings.")
    if not config.get("plex_library_section_id"):
        raise HTTPException(status_code=400, detail="Plex music library section ID not set in Settings.")

    client = PlexClient(
        config["plex_url"],
        config["plex_token"],
        config["plex_library_section_id"],
    )

    matched_keys, unmatched, total = await client.match_tracks(req.tracks)

    config = load_config()
    plex_playlist_id = None
    if matched_keys:
        plex_name = _plex_playlist_name(req.playlist_name, config)
        plex_playlist_id = await client.create_playlist(plex_name, matched_keys)
        if req.digarr_playlist_id:
            update_playlist_plex_result(req.digarr_playlist_id, plex_playlist_id,
                                        len(matched_keys), total, unmatched, plex_playlist_name=plex_name)

    return {
        "matched": len(matched_keys),
        "total": total,
        "unmatched": unmatched,
        "plex_playlist_id": plex_playlist_id,
        "message": f"{len(matched_keys)}/{total} tracks matched in Plex",
    }

async def _do_sync_plex_playlist(pl: dict, plex_client: PlexClient, all_lidarr_artists: list = None, config: dict = None) -> dict:
    """Core sync logic — shared by single-playlist and sync-all endpoints."""
    if config is None:
        config = load_config()
    playlist_id = pl["id"]
    tracks = pl.get("tracks", [])

    # Apply manual matches before hitting the Plex search API.
    # Tracks with a confirmed match skip the search entirely.
    pairs = [(t.get("artist", ""), t.get("title", "")) for t in tracks]
    manual = db_get_manual_matches(pairs, "plex")
    pre_matched_keys = []
    auto_tracks = []
    for t in tracks:
        key = (_db_norm(t.get("artist", "")), _db_norm(t.get("title", "")))
        if key in manual:
            pre_matched_keys.append(manual[key])
        else:
            auto_tracks.append(t)

    # Check the local track_cache before making live Plex API calls.
    # When the cache is warm this turns most matches into simple DB lookups.
    cache_matched_keys = []
    live_tracks = []
    for t in auto_tracks:
        cached_id = db_lookup_track_cache(t.get("artist", ""), t.get("title", ""), "plex")
        if cached_id:
            cache_matched_keys.append(cached_id)
        else:
            live_tracks.append(t)

    live_matched_keys, unmatched, _ = await plex_client.match_tracks(live_tracks)
    matched_keys = pre_matched_keys + cache_matched_keys + live_matched_keys
    total = len(tracks)

    old_plex_id = pl.get("plex_playlist_id")
    if old_plex_id:
        try:
            await plex_client.delete_playlist(old_plex_id)
        except Exception:
            pass

    plex_playlist_id = None
    if matched_keys:
        plex_name = _plex_playlist_name(pl["name"], config)
        plex_playlist_id = await plex_client.create_playlist(plex_name, matched_keys)
        update_playlist_plex_result(playlist_id, plex_playlist_id,
                                    len(matched_keys), total, unmatched, plex_playlist_name=plex_name)

    lidarr_monitored = []
    if unmatched and all_lidarr_artists is not None:
        config = load_config()
        if config.get("lidarr_url") and config.get("lidarr_api_key"):
            lidarr = make_lidarr_client(config)
            seen = set()
            tasks = []
            for track in unmatched:
                artist = (track.get("artist") or "").strip()
                album = (track.get("album") or "").strip() or None
                key = (artist.lower(), (album or "").lower())
                if not artist or key in seen:
                    continue
                seen.add(key)
                tasks.append(lidarr.ensure_album_monitored_with_library(artist, album, all_lidarr_artists))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if not isinstance(result, BaseException) and result["status"] == "monitored":
                    lidarr_monitored.append({"artist": result["artist"], "album": result["album"]})

    return {
        "matched": len(matched_keys),
        "total": total,
        "unmatched": unmatched,
        "plex_playlist_id": plex_playlist_id,
        "lidarr_monitored": lidarr_monitored,
        "message": f"{len(matched_keys)}/{total} tracks matched in Plex",
    }


@app.post("/api/plex/playlist/{playlist_id}/sync")
async def sync_plex_playlist(playlist_id: int):
    config = load_config()
    if not config.get("plex_url") or not config.get("plex_token"):
        raise HTTPException(status_code=400, detail="Plex not configured. Add your Plex URL and token in Settings.")
    if not config.get("plex_library_section_id"):
        raise HTTPException(status_code=400, detail="Plex music library section ID not set in Settings.")

    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")

    plex_client = PlexClient(config["plex_url"], config["plex_token"], config["plex_library_section_id"])

    all_lidarr_artists = None
    if config.get("lidarr_url") and config.get("lidarr_api_key"):
        all_lidarr_artists = await make_lidarr_client(config).get_all_artists()

    return await _do_sync_plex_playlist(pl, plex_client, all_lidarr_artists, config)


@app.post("/api/plex/sync-all")
async def sync_all_plex():
    config = load_config()
    if not config.get("plex_url") or not config.get("plex_token"):
        raise HTTPException(status_code=400, detail="Plex not configured.")
    if not config.get("plex_library_section_id"):
        raise HTTPException(status_code=400, detail="Plex library section ID not set.")

    playlists = [pl for pl in get_playlists() if pl.get("plex_playlist_id")]
    if not playlists:
        return {"synced": 0, "results": []}

    # Build shared clients — one Plex client, one Lidarr library fetch for all playlists
    plex_client = PlexClient(config["plex_url"], config["plex_token"], config["plex_library_section_id"])
    all_lidarr_artists = None
    if config.get("lidarr_url") and config.get("lidarr_api_key"):
        all_lidarr_artists = await make_lidarr_client(config).get_all_artists()

    # Fetch full playlist records (get_playlists omits tracks)
    full_playlists = [get_playlist(pl["id"]) for pl in playlists]

    semaphore = asyncio.Semaphore(3)

    async def sync_one(pl):
        async with semaphore:
            try:
                result = await _do_sync_plex_playlist(pl, plex_client, all_lidarr_artists, config)
                return {"id": pl["id"], "name": pl["name"], "status": "ok", **result}
            except Exception as e:
                return {"id": pl["id"], "name": pl["name"], "status": "error", "error": str(e)}

    results = await asyncio.gather(*[sync_one(pl) for pl in full_playlists])
    return {"synced": len(results), "results": list(results)}


# ---------------------------------------------------------------------------
# Jellyfin endpoints
# ---------------------------------------------------------------------------

@app.get("/api/jellyfin/status")
async def jellyfin_status():
    config = load_config()
    if not config.get("jellyfin_url") or not config.get("jellyfin_api_key"):
        return {"configured": False}
    try:
        jf = JellyfinClient(config["jellyfin_url"], config["jellyfin_api_key"])
        info = await jf.test_connection()
        return {"configured": True, **info}
    except Exception as e:
        return {"configured": True, "error": str(e)}


async def _do_sync_jellyfin_playlist(pl: dict, jf: JellyfinClient, config: dict) -> dict:
    playlist_id = pl["id"]
    tracks = pl.get("tracks", [])
    cache_matched, live_tracks = [], []
    for t in tracks:
        cached = db_lookup_track_cache(t.get("artist", ""), t.get("title", ""), "jellyfin")
        if cached:
            cache_matched.append(cached)
        else:
            live_tracks.append(t)
    live_matched, unmatched, _ = await jf.match_tracks(live_tracks)
    matched_ids = cache_matched + live_matched
    total = len(tracks)
    jf_name = _jellyfin_playlist_name(pl["name"], config)
    if pl.get("jellyfin_playlist_id"):
        await jf.update_playlist(pl["jellyfin_playlist_id"], matched_ids)
        jellyfin_playlist_id = pl["jellyfin_playlist_id"]
    else:
        if not matched_ids:
            raise ValueError("No tracks matched in Jellyfin — cannot create playlist.")
        jellyfin_playlist_id = await jf.create_playlist(jf_name, matched_ids)
    update_playlist_jellyfin_result(playlist_id, jellyfin_playlist_id, len(matched_ids), total)
    return {
        "matched": len(matched_ids),
        "total": total,
        "jellyfin_playlist_id": jellyfin_playlist_id,
        "unmatched": unmatched,
        "message": f"{len(matched_ids)}/{total} tracks matched in Jellyfin",
    }


@app.post("/api/jellyfin/playlist/{playlist_id}/sync")
async def sync_jellyfin_playlist(playlist_id: int):
    config = load_config()
    if not config.get("jellyfin_url") or not config.get("jellyfin_api_key"):
        raise HTTPException(status_code=400, detail="Jellyfin not configured. Add your Jellyfin URL and API key in Settings.")
    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    jf = JellyfinClient(config["jellyfin_url"], config["jellyfin_api_key"])
    try:
        return await _do_sync_jellyfin_playlist(pl, jf, config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/jellyfin/sync-all")
async def sync_all_jellyfin():
    config = load_config()
    if not config.get("jellyfin_url") or not config.get("jellyfin_api_key"):
        raise HTTPException(status_code=400, detail="Jellyfin not configured.")
    playlists = [pl for pl in get_playlists() if pl.get("jellyfin_playlist_id")]
    if not playlists:
        return {"synced": 0, "results": []}
    jf = JellyfinClient(config["jellyfin_url"], config["jellyfin_api_key"])
    full_playlists = [get_playlist(pl["id"]) for pl in playlists]
    semaphore = asyncio.Semaphore(3)
    async def sync_one(pl):
        async with semaphore:
            try:
                result = await _do_sync_jellyfin_playlist(pl, jf, config)
                return {"id": pl["id"], "name": pl["name"], "status": "ok", **result}
            except Exception as e:
                return {"id": pl["id"], "name": pl["name"], "status": "error", "error": str(e)}
    results = await asyncio.gather(*[sync_one(pl) for pl in full_playlists])
    return {"synced": len(results), "results": list(results)}


# Jellyfin library cache
_jellyfin_cache_refresh_task: asyncio.Task = None
_jellyfin_cache_refresh_state: dict = {"state": "idle", "error": None}

async def _run_jellyfin_cache_refresh() -> None:
    global _jellyfin_cache_refresh_state
    _jellyfin_cache_refresh_state = {"state": "running", "error": None}
    try:
        config = load_config()
        client = JellyfinMediaClient(config["jellyfin_url"], config["jellyfin_api_key"])
        tracks = await client.get_all_tracks()
        db_upsert_track_cache("jellyfin", tracks)
        _jellyfin_cache_refresh_state = {"state": "idle", "error": None}
        logger.info("Jellyfin cache refreshed: %d tracks", len(tracks))
    except asyncio.CancelledError:
        _jellyfin_cache_refresh_state = {"state": "idle", "error": None}
        raise
    except Exception as exc:
        logger.error("Jellyfin cache refresh failed: %s", exc)
        _jellyfin_cache_refresh_state = {"state": "error", "error": str(exc)}

@app.post("/api/jellyfin/cache/refresh")
async def refresh_jellyfin_cache():
    global _jellyfin_cache_refresh_task
    config = load_config()
    if not config.get("jellyfin_url") or not config.get("jellyfin_api_key"):
        raise HTTPException(status_code=400, detail="Jellyfin not configured.")
    if _jellyfin_cache_refresh_task and not _jellyfin_cache_refresh_task.done():
        _jellyfin_cache_refresh_task.cancel()
        try:
            await _jellyfin_cache_refresh_task
        except asyncio.CancelledError:
            pass
    _jellyfin_cache_refresh_task = asyncio.create_task(_run_jellyfin_cache_refresh())
    return {"status": "started"}

@app.get("/api/jellyfin/cache/status")
async def jellyfin_cache_status():
    stats = db_get_cache_stats("jellyfin")
    return {**stats, "refresh_state": _jellyfin_cache_refresh_state["state"], "refresh_error": _jellyfin_cache_refresh_state.get("error")}


# ---------------------------------------------------------------------------
# Navidrome endpoints
# ---------------------------------------------------------------------------

@app.get("/api/navidrome/status")
async def navidrome_status():
    config = load_config()
    if not config.get("navidrome_url") or not config.get("navidrome_username"):
        return {"configured": False}
    try:
        nd = NavidromeClient(
            config["navidrome_url"], config["navidrome_username"],
            config.get("navidrome_password", ""),
        )
        info = await nd.test_connection()
        return {"configured": True, **info}
    except Exception as e:
        return {"configured": True, "error": str(e)}


async def _do_sync_navidrome_playlist(pl: dict, nd: NavidromeClient, config: dict) -> dict:
    playlist_id = pl["id"]
    tracks = pl.get("tracks", [])
    cache_matched, live_tracks = [], []
    for t in tracks:
        cached = db_lookup_track_cache(t.get("artist", ""), t.get("title", ""), "navidrome")
        if cached:
            cache_matched.append(cached)
        else:
            live_tracks.append(t)
    live_matched, unmatched, _ = await nd.match_tracks(live_tracks)
    matched_ids = cache_matched + live_matched
    total = len(tracks)
    nd_name = _navidrome_playlist_name(pl["name"], config)
    if pl.get("navidrome_playlist_id"):
        await nd.update_playlist(pl["navidrome_playlist_id"], nd_name, matched_ids)
        navidrome_playlist_id = pl["navidrome_playlist_id"]
    else:
        if not matched_ids:
            raise ValueError("No tracks matched in Navidrome — cannot create playlist.")
        navidrome_playlist_id = await nd.create_playlist(nd_name, matched_ids)
    update_playlist_navidrome_result(playlist_id, navidrome_playlist_id, len(matched_ids), total)
    return {
        "matched": len(matched_ids),
        "total": total,
        "navidrome_playlist_id": navidrome_playlist_id,
        "unmatched": unmatched,
        "message": f"{len(matched_ids)}/{total} tracks matched in Navidrome",
    }


@app.post("/api/navidrome/playlist/{playlist_id}/sync")
async def sync_navidrome_playlist(playlist_id: int):
    config = load_config()
    if not config.get("navidrome_url") or not config.get("navidrome_username"):
        raise HTTPException(status_code=400, detail="Navidrome not configured. Add your Navidrome URL and credentials in Settings.")
    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    nd = NavidromeClient(config["navidrome_url"], config["navidrome_username"], config.get("navidrome_password", ""))
    try:
        return await _do_sync_navidrome_playlist(pl, nd, config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/navidrome/sync-all")
async def sync_all_navidrome():
    config = load_config()
    if not config.get("navidrome_url") or not config.get("navidrome_username"):
        raise HTTPException(status_code=400, detail="Navidrome not configured.")
    playlists = [pl for pl in get_playlists() if pl.get("navidrome_playlist_id")]
    if not playlists:
        return {"synced": 0, "results": []}
    nd = NavidromeClient(config["navidrome_url"], config["navidrome_username"], config.get("navidrome_password", ""))
    full_playlists = [get_playlist(pl["id"]) for pl in playlists]
    semaphore = asyncio.Semaphore(3)
    async def sync_one(pl):
        async with semaphore:
            try:
                result = await _do_sync_navidrome_playlist(pl, nd, config)
                return {"id": pl["id"], "name": pl["name"], "status": "ok", **result}
            except Exception as e:
                return {"id": pl["id"], "name": pl["name"], "status": "error", "error": str(e)}
    results = await asyncio.gather(*[sync_one(pl) for pl in full_playlists])
    return {"synced": len(results), "results": list(results)}


# Navidrome library cache
_navidrome_cache_refresh_task: asyncio.Task = None
_navidrome_cache_refresh_state: dict = {"state": "idle", "error": None}

async def _run_navidrome_cache_refresh() -> None:
    global _navidrome_cache_refresh_state
    _navidrome_cache_refresh_state = {"state": "running", "error": None}
    try:
        config = load_config()
        client = NavidromeMediaClient(config["navidrome_url"], config["navidrome_username"], config.get("navidrome_password", ""))
        tracks = await client.get_all_tracks()
        db_upsert_track_cache("navidrome", tracks)
        _navidrome_cache_refresh_state = {"state": "idle", "error": None}
        logger.info("Navidrome cache refreshed: %d tracks", len(tracks))
    except asyncio.CancelledError:
        _navidrome_cache_refresh_state = {"state": "idle", "error": None}
        raise
    except Exception as exc:
        logger.error("Navidrome cache refresh failed: %s", exc)
        _navidrome_cache_refresh_state = {"state": "error", "error": str(exc)}

@app.post("/api/navidrome/cache/refresh")
async def refresh_navidrome_cache():
    global _navidrome_cache_refresh_task
    config = load_config()
    if not config.get("navidrome_url") or not config.get("navidrome_username"):
        raise HTTPException(status_code=400, detail="Navidrome not configured.")
    if _navidrome_cache_refresh_task and not _navidrome_cache_refresh_task.done():
        _navidrome_cache_refresh_task.cancel()
        try:
            await _navidrome_cache_refresh_task
        except asyncio.CancelledError:
            pass
    _navidrome_cache_refresh_task = asyncio.create_task(_run_navidrome_cache_refresh())
    return {"status": "started"}

@app.get("/api/navidrome/cache/status")
async def navidrome_cache_status():
    stats = db_get_cache_stats("navidrome")
    return {**stats, "refresh_state": _navidrome_cache_refresh_state["state"], "refresh_error": _navidrome_cache_refresh_state.get("error")}


# ---------------------------------------------------------------------------
# Deemix endpoints
# ---------------------------------------------------------------------------

@app.get("/api/deemix/status")
async def deemix_status():
    config = load_config()
    if not config.get("deemix_url"):
        return {"configured": False}
    try:
        dx = DeemixClient(config["deemix_url"])
        info = await dx.test_connection()
        return {"configured": True, **info}
    except Exception as e:
        return {"configured": True, "error": str(e)}


# ---------------------------------------------------------------------------
# slskd endpoints
# ---------------------------------------------------------------------------

@app.get("/api/slskd/status")
async def slskd_status():
    config = load_config()
    if not config.get("slskd_url") or not config.get("slskd_api_key"):
        return {"configured": False}
    try:
        sl = SlskdClient(config["slskd_url"], config["slskd_api_key"])
        info = await sl.test_connection()
        return {"configured": True, **info}
    except Exception as e:
        return {"configured": True, "error": str(e)}


@app.get("/api/playlists/{playlist_id}/slskd-flagged")
def get_slskd_flagged(playlist_id: int):
    """Return the list of flagged Soulseek tracks for manual review."""
    import json as _json
    pl = get_playlist(playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    raw = pl.get("slskd_flagged_tracks") or "[]"
    if isinstance(raw, str):
        try:
            flagged = _json.loads(raw)
        except Exception:
            flagged = []
    else:
        flagged = raw
    return {
        "flagged": flagged,
        "queued": pl.get("slskd_queued_count") or 0,
        "flagged_count": pl.get("slskd_flagged_count") or 0,
        "total": pl.get("slskd_total_count") or 0,
    }


class SlskdManualQueueRequest(BaseModel):
    artist: str = Field(max_length=500)
    title: str = Field(max_length=500)
    username: str = Field(max_length=200)
    filename: str = Field(max_length=2000)
    size: int = Field(default=0, ge=0)


@app.post("/api/slskd/queue")
@limiter.limit("30/minute")
async def slskd_manual_queue(req: SlskdManualQueueRequest, request: Request):
    """Manually queue a specific Soulseek file that was flagged for review."""
    config = load_config()
    if not config.get("slskd_url") or not config.get("slskd_api_key"):
        raise HTTPException(status_code=400, detail="slskd not configured.")
    sl = SlskdClient(config["slskd_url"], config["slskd_api_key"])
    try:
        await sl._queue_download(req.username, {"filename": req.filename, "size": req.size})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"slskd download failed: {exc}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Library cache endpoints
# ---------------------------------------------------------------------------

# Background refresh state — one refresh runs at a time across the process.
_cache_refresh_task: asyncio.Task = None
_cache_refresh_state: dict = {"state": "idle", "error": None}


async def _run_cache_refresh() -> None:
    global _cache_refresh_state
    _cache_refresh_state = {"state": "running", "error": None}
    try:
        config = load_config()
        client = PlexMediaClient(
            config["plex_url"],
            config["plex_token"],
            config["plex_library_section_id"],
        )
        tracks = await client.get_all_tracks()
        db_upsert_track_cache("plex", tracks)
        _cache_refresh_state = {"state": "idle", "error": None}
        logger.info("Library cache refreshed: %d tracks", len(tracks))
    except asyncio.CancelledError:
        _cache_refresh_state = {"state": "idle", "error": None}
        raise
    except Exception as exc:
        logger.error("Library cache refresh failed: %s", exc)
        _cache_refresh_state = {"state": "error", "error": str(exc)}


@app.post("/api/library/cache/refresh")
async def refresh_library_cache():
    """Start a background library cache refresh. Cancels any in-progress refresh first."""
    global _cache_refresh_task

    config = load_config()
    if not config.get("plex_url") or not config.get("plex_token"):
        raise HTTPException(status_code=400, detail="Plex not configured. Add your Plex URL and token in Settings.")
    if not config.get("plex_library_section_id"):
        raise HTTPException(status_code=400, detail="Plex music library section ID not set in Settings.")

    # Cancel any in-progress refresh before starting a new one.
    if _cache_refresh_task and not _cache_refresh_task.done():
        _cache_refresh_task.cancel()
        try:
            await _cache_refresh_task
        except asyncio.CancelledError:
            pass

    _cache_refresh_task = asyncio.create_task(_run_cache_refresh())
    return {"status": "started"}


@app.get("/api/library/cache/status")
async def library_cache_status():
    """Return cached track count, last-refreshed timestamp, and current refresh state."""
    stats = db_get_cache_stats("plex")
    return {**stats, "refresh_state": _cache_refresh_state["state"], "refresh_error": _cache_refresh_state.get("error")}


@app.get("/api/library/search")
async def search_library(q: str, source: str = "plex", limit: int = 20):
    """
    Search the local track cache.
    Falls back to a live Plex hub search when the cache is empty.
    """
    if not q or len(q.strip()) < 2:
        return {"results": [], "source": "cache"}

    results = db_search_track_cache(q.strip(), source=source, limit=limit)

    if not results:
        config = load_config()
        try:
            if source == "jellyfin" and config.get("jellyfin_url") and config.get("jellyfin_api_key"):
                client = JellyfinMediaClient(config["jellyfin_url"], config["jellyfin_api_key"])
                results = await client.search_tracks(q.strip(), limit=limit)
                return {"results": results, "source": "live"}
            elif source == "navidrome" and config.get("navidrome_url") and config.get("navidrome_username"):
                client = NavidromeMediaClient(config["navidrome_url"], config["navidrome_username"], config.get("navidrome_password", ""))
                results = await client.search_tracks(q.strip(), limit=limit)
                return {"results": results, "source": "live"}
            elif config.get("plex_url") and config.get("plex_token") and config.get("plex_library_section_id"):
                client = PlexMediaClient(config["plex_url"], config["plex_token"], config["plex_library_section_id"])
                results = await client.search_tracks(q.strip(), limit=limit)
                return {"results": results, "source": "live"}
        except Exception:
            pass

    return {"results": results, "source": "cache"}


@app.get("/api/library/ignored-tracks")
async def get_ignored_tracks():
    return {"ignored": db_get_ignored_tracks()}


@app.post("/api/library/ignore-track")
async def ignore_track(body: dict):
    artist = (body.get("artist") or "").strip()
    title = (body.get("title") or "").strip()
    if not artist and not title:
        raise HTTPException(status_code=400, detail="artist or title required")
    db_ignore_track(artist, title)
    return {"status": "ok"}


@app.delete("/api/library/ignore-track")
async def unignore_track(body: dict):
    artist = (body.get("artist") or "").strip()
    title = (body.get("title") or "").strip()
    db_unignore_track(artist, title)
    return {"status": "ok"}


@app.post("/api/library/manual-match")
async def save_manual_match(body: dict):
    """Persist a user-confirmed track → library mapping."""
    artist = (body.get("artist") or "").strip()
    title = (body.get("title") or "").strip()
    external_id = (body.get("external_id") or "").strip()
    source = (body.get("source") or "plex").strip()
    if not artist or not title or not external_id:
        raise HTTPException(status_code=400, detail="artist, title, and external_id are required")
    db_set_manual_match(artist, title, external_id, source)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Download queue endpoints
# ---------------------------------------------------------------------------

@app.get("/api/download/queue")
async def get_download_queue(refresh: bool = False):
    """
    Return the current download queue.
    Uses the cached snapshot unless refresh=true.
    """
    if refresh:
        config = load_config()
        if config.get("lidarr_url") and config.get("lidarr_api_key"):
            try:
                lidarr = make_lidarr_client(config)
                dl_client = LidarrDownloadClient(lidarr)
                items = await dl_client.get_queue_status()
                db_upsert_download_queue_cache("lidarr", items)
                return {"items": items, "source": "live"}
            except Exception as exc:
                pass
    cached = db_get_download_queue_cache("lidarr")
    return {"items": cached, "source": "cache"}


@app.post("/api/download/search")
async def download_search(body: dict):
    """Search the download catalog (MusicBrainz via Lidarr). Results are cached."""
    artist = (body.get("artist") or "").strip()
    album = (body.get("album") or "").strip() or None
    if not artist:
        raise HTTPException(status_code=400, detail="artist is required")

    cache_key = f"{artist}|{album or ''}".lower()
    cached = db_get_download_search_cache(cache_key, "lidarr")
    if cached is not None:
        return {"results": cached, "source": "cache"}

    config = load_config()
    if not config.get("lidarr_url") or not config.get("lidarr_api_key"):
        raise HTTPException(status_code=400, detail="Lidarr not configured.")

    lidarr = make_lidarr_client(config)
    dl_client = LidarrDownloadClient(lidarr)
    results = await dl_client.search(artist, album)
    db_upsert_download_search_cache(cache_key, "lidarr", results)
    return {"results": results, "source": "live"}


# ---------------------------------------------------------------------------
# Discover endpoints — ListenBrainz & Last.fm
# ---------------------------------------------------------------------------

_LB_VALID_TYPES = ("weekly_jams", "daily_jams", "weekly_exploration")

@app.get("/api/discover/listenbrainz/recommendations")
async def discover_lb_recommendations(type: str = "weekly_jams"):
    if type not in _LB_VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"type must be one of: {', '.join(_LB_VALID_TYPES)}")
    config = load_config()
    username = config.get("listenbrainz_username", "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="ListenBrainz username not configured. Add it in Settings.")
    try:
        return await lb_recommendation(username, type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=400, detail=f"ListenBrainz user '{username}' not found.")
        raise HTTPException(status_code=400, detail=f"ListenBrainz API error ({e.response.status_code})")


async def _compute_similar_to_library(api_key: str, library_artist_names: list) -> dict:
    """
    Fetch similar artists for up to 75 library artists (semaphore=5),
    count how many library artists each similar artist appeared for,
    filter out artists already in the library, return top 50 by count.
    """
    import random
    from collections import Counter

    sample = (
        random.sample(library_artist_names, 75)
        if len(library_artist_names) > 75
        else library_artist_names
    )
    library_norm = {normalize(a) for a in library_artist_names}
    counts: Counter = Counter()
    sem = asyncio.Semaphore(5)

    async def _fetch_one(artist_name: str):
        async with sem:
            try:
                similar = await lfm_get_similar_artists(api_key, artist_name, limit=15)
                for s in similar:
                    name = s["name"]
                    if normalize(name) not in library_norm:
                        counts[name] += 1
            except Exception:
                pass  # best-effort — unknown artists, rate limits, etc.

    await asyncio.gather(*[_fetch_one(a) for a in sample])

    sorted_artists = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    artists = [{"name": name, "similarity_count": count} for name, count in sorted_artists[:50]]

    return {"name": "Similar to Library", "artists": artists, "tracks": []}


@app.get("/api/discover/similar-to-library")
async def discover_similar_to_library():
    config = load_config()
    api_key = config.get("lastfm_api_key", "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Last.fm API key required. Add it in Settings.")
    if not config.get("lidarr_url") or not config.get("lidarr_api_key"):
        raise HTTPException(status_code=400, detail="Lidarr not configured. Add your Lidarr URL and API key in Settings.")
    try:
        all_lidarr = await make_lidarr_client(config).get_all_artists()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not reach Lidarr: {e}")
    artist_names = [a.get("artistName", "") for a in all_lidarr if a.get("artistName")]
    if not artist_names:
        raise HTTPException(status_code=400, detail="No artists found in your Lidarr library.")
    try:
        return await _compute_similar_to_library(api_key, artist_names)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Similar artist lookup failed: {e}")


@app.get("/api/discover/discogs/wantlist")
async def discover_discogs_wantlist():
    config = load_config()
    token    = config.get("discogs_token", "").strip()
    username = config.get("discogs_username", "").strip()
    if not token or not username:
        raise HTTPException(status_code=400, detail="Discogs username and token required. Add them in Settings.")
    try:
        return await discogs_get_wantlist(token, username)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=400, detail=f"Discogs user '{username}' not found.")
        if e.response.status_code == 401:
            raise HTTPException(status_code=400, detail="Discogs token invalid. Check your token in Settings.")
        raise HTTPException(status_code=400, detail=f"Discogs API error ({e.response.status_code})")


if __name__ == "__main__":
    _port = int(os.environ.get("PORT", 8090))
    uvicorn.run("main:app", host="0.0.0.0", port=_port, reload=True)

