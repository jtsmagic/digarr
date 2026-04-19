import json
import os
import stat

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
    "refresh_interval_hours": 0,
    "refresh_last_run": None,
    "refresh_last_run_summary": [],
    "refresh_excluded_playlist_ids": [],
    "refresh_delay_between_playlists": 0,
    "refresh_max_new_artists": 0,
    "refresh_webhook_on_changes_only": False,
    "playlist_export_path": "",
    "artist_blocklist": [],
    "webhook_url": "",
    "refresh_merge_tracks": False,
    "plex_sync_interval_hours": 0,
    "jellyfin_url": "",
    "jellyfin_api_key": "",
    "jellyfin_sync_interval_hours": 0,
    "jellyfin_append_digarr": True,
    "jellyfin_delete_on_remove": False,
    "navidrome_url": "",
    "navidrome_username": "",
    "navidrome_password": "",
    "navidrome_sync_interval_hours": 0,
    "navidrome_append_digarr": True,
    "navidrome_delete_on_remove": False,
    "auth_username": "",
    "hashed_password": "",
    "oidc_issuer": "",
    "oidc_client_id": "",
    "oidc_client_secret": "",
    "oidc_redirect_uri": "",
    "deemix_url": "",
    "deemix_arl": "",
    "slskd_url": "",
    "slskd_api_key": "",
    "slskd_confidence_threshold": 75,
    "slskd_lidarr_import_folder": "",
}

# Environment variable overrides for sensitive keys.
# Set these instead of storing secrets in config.json (e.g. via Docker --env-file or secrets).
_ENV_OVERRIDES: dict[str, str] = {
    "anthropic_api_key":    "DIGARR_ANTHROPIC_KEY",
    "openai_api_key":       "DIGARR_OPENAI_KEY",
    "lidarr_api_key":       "DIGARR_LIDARR_KEY",
    "plex_token":           "DIGARR_PLEX_TOKEN",
    "spotify_client_id":    "DIGARR_SPOTIFY_CLIENT_ID",
    "spotify_client_secret":"DIGARR_SPOTIFY_CLIENT_SECRET",
    "lastfm_api_key":       "DIGARR_LASTFM_KEY",
}


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        config = DEFAULTS.copy()
    else:
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            config = {**DEFAULTS, **data}
        except Exception:
            config = DEFAULTS.copy()
    # Env vars take precedence over stored values for sensitive keys
    for key, env_var in _ENV_OVERRIDES.items():
        val = os.environ.get(env_var, "")
        if val:
            config[key] = val
    return config


def save_config(config: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    existing = load_config()
    existing.update(config)
    path = CONFIG_PATH
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)
    # Restrict to owner read/write only — no world or group access
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

