import json
import os

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/data/config.json")

DEFAULTS = {
    "timezone": "America/Chicago",
    "anthropic_api_key": "",
    "openai_api_key": "",
    "active_ai_provider": "claude",
    "claude_model": "claude-sonnet-4-6",
    "openai_model": "gpt-4o-mini",
    "lidarr_url": "",
    "lidarr_api_key": "",
    "lidarr_quality_profile_id": "1",
    "lidarr_metadata_profile_id": "1",
    "lidarr_root_folder": "/music",
    "plex_url": "",
    "plex_token": "",
    "plex_library_section_id": "",
    "plex_append_digarr": True,
    "plex_delete_on_remove": False,
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "spotify_redirect_uri": "",
    "spotify_access_token": "",
    "spotify_refresh_token": "",
    "spotify_token_expires_at": None,
    "spotify_user_id": "",
    "spotify_display_name": "",
    "listenbrainz_username": "",
    "lastfm_api_key": "",
    "discogs_username": "",
    "discogs_token": "",
    "refresh_interval_hours": 0,
    "refresh_last_run": None,
    "refresh_last_run_summary": [],
    "refresh_excluded_playlist_ids": [],
    "playlist_export_path": "",
    "artist_blocklist": [],
    "webhook_url": "",
    "refresh_merge_tracks": False,
    "plex_sync_interval_hours": 0,
    "auth_username": "",
    "hashed_password": "",
    "oidc_issuer": "",
    "oidc_client_id": "",
    "oidc_client_secret": "",
    "oidc_redirect_uri": "",
}

def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return DEFAULTS.copy()
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
        # Merge with defaults for any missing keys
        return {**DEFAULTS, **data}
    except Exception:
        return DEFAULTS.copy()

def save_config(config: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    # Merge with existing
    existing = load_config()
    existing.update(config)
    with open(CONFIG_PATH, "w") as f:
        json.dump(existing, f, indent=2)

