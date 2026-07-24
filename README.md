# Digarr ⦿

**The crates don't fill themselves.**

Digarr is a self-hosted web app that imports artists and playlists using AI to parse any source — blog posts, Reddit threads, M3U files, URLs, raw text lists, and more. It feeds [Lidarr](https://lidarr.audio) and [Deemix](https://deemix.app) automatically — both are optional.

![Digarr](https://img.shields.io/badge/arr-ecosystem-orange?style=flat-square) ![Docker](https://img.shields.io/badge/docker-ready-blue?style=flat-square) ![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-yellow?style=flat-square&logo=buy-me-a-coffee)](https://www.buymeacoffee.com/jtsmagic)

---

## Features

- **Parse anything** — paste a URL, upload an M3U, drop in a raw text list, or use a Spotify playlist URL; M3U URLs are auto-detected, no separate tab needed
- **AI-powered extraction** — Claude or OpenAI identifies artists and tracks from unstructured content; confidence scores dim low-confidence results so you can review before adding
- **Site scripts** — drop a small parser into `backend/parsers/scripts/` for a specific site (e.g. a "recently played" page) and Digarr uses it instead of AI for matching URLs, on both import and every refresh — zero AI cost for sites you've written one for. See [Site scripts](#site-scripts-no-ai-parsing-for-specific-sources) below.
- **Lidarr integration** — search, check library, and add artists in one click; fully optional
- **Deemix integration** — automatically queue every playlist track to Deezer/downloads via a self-hosted Deemix instance during import
- **Track status** — see which tracks are downloaded (green/yellow/red) vs missing
- **Manual track matching** — search your Plex, Jellyfin, or Navidrome library cache for unmatched tracks and confirm the right match; matches persist across refreshes
- **M3U / JSPF export** — download parsed playlists as M3U or JSON (JSPF) files
- **Plex integration** — push playlists directly to Plex; unmatched tracks trigger Lidarr monitoring automatically; Sync All re-syncs every playlist in one shot
- **Jellyfin integration** — push and sync playlists to Jellyfin; library cache for fast matching; scheduled sync interval
- **Navidrome integration** — push and sync playlists via Subsonic API; library cache for fast matching; scheduled sync interval
- **Spotify integration** — OAuth (PKCE) for importing your playlists and Liked Songs; push any Digarr playlist to Spotify from History
- **Playlist history** — every import saved locally with full track/artist detail; inline rename, delete, and refresh
- **Background import queue** — imports run in the background with a live progress bar; navigate away and come back without losing state
- **Playlist refresh** — re-fetch any source URL, add net-new artists to Lidarr, and re-sync all connected media servers; merge mode appends new tracks rather than replacing
- **Scheduled refresh** — auto-refresh all playlists on a configurable interval (1h through bi-weekly); per-playlist include/exclude control; webhook fires after each run
- **Scheduled media server sync** — independent sync intervals for Plex, Jellyfin, and Navidrome; fills playlists in as Lidarr finishes downloading
- **Discover page** — curated feeds from Spotify, ListenBrainz, and Similar to Library; review recommendations with library status badges and import directly
- **Wanted/missing report** — see which Lidarr artists added via Digarr still have undownloaded albums
- **Artist blocklist** — permanently ignore specific artists across all imports and refreshes
- **MCP server** — control Digarr from Claude Desktop or Claude Code, over local stdio (`docker exec`) or a remote OAuth-protected HTTPS endpoint: feed it a link and have it import the playlist, check whether you already have a song/artist, or pull the Lidarr wanted report, all from chat
- **Authentication** — password login (bcrypt) and/or OIDC SSO (Authentik, Keycloak, etc.); both can be active simultaneously; 30-day sessions
- **Multi-AI support** — Claude (Haiku/Sonnet/Opus) and OpenAI (GPT-4o mini/GPT-4o), switchable from Settings with per-provider model selection
- **Clean web UI** — dark, vinyl-inspired interface

---

## Quick Start

### Requirements
- Docker + Docker Compose
- Anthropic API key ([get one here](https://console.anthropic.com)) — or OpenAI key
- Everything else (Lidarr, Deemix, Plex, Jellyfin, Navidrome, Spotify) is optional

### Run with Docker Compose

```bash
git clone https://github.com/jtsmagic/digarr.git
cd digarr
docker compose up -d
```

Then open **http://localhost:8090** and go to **Settings** to configure:

1. Your Anthropic API key
2. Your Lidarr URL + API key
3. Click **Load Profiles from Lidarr** to auto-populate quality/metadata profiles

That's it.

---

## Usage

### Import from a URL
Paste any URL — a Pitchfork best-of list, a music blog, a Reddit thread — and Digarr will fetch the page and use Claude to extract every artist and track mentioned. M3U URLs (`.m3u`, `.m3u8`) are auto-detected and parsed directly without AI.

### Site scripts (no-AI parsing for specific sources)
For a URL you import/refresh often — a radio station's "recently played" page, a specific blog's format, anything with a consistent structure — you can write a small deterministic parser instead of paying for AI extraction every time. Drop a `.py` file into `backend/parsers/scripts/` defining:

```python
URL_PATTERN = r"https?://example\.com/some/path"

async def parse(url: str) -> list[dict]:
    # fetch/scrape `url` however this site needs, return one dict per track:
    return [{"artist": "...", "title": "...", "album": None}, ...]
```

Any playlist whose source URL matches `URL_PATTERN` uses that script instead of Digarr's AI provider — on the initial import *and* every subsequent refresh (manual or scheduled), so it's zero AI cost going forward, not just a one-time bypass. `parsers/text.fetch_raw_html(url, user_agent=...)` is available for scripts that need real HTML markup rather than AI-oriented plain text. See `backend/parsers/scripts/xmplaylist.py` for a working example (a "recently played" radio station page, parsed via a repeating HTML structure). If a script returns fewer than 3 tracks, Digarr treats it as a failure (site likely changed or blocked the request) rather than wiping the playlist.

### Import from M3U file
Upload an M3U file or drag-and-drop it. Digarr parses the `#EXTINF` tags directly, no AI needed.

### Import from text
Paste a raw list of artists or songs. Claude will figure out the structure.

### Export M3U / JSPF
Any parsed playlist can be downloaded as an M3U or JSPF file for use in other players.

### Deemix (automatic Deezer queueing)
Connect a self-hosted [Deemix](https://deemix.app) instance in **Settings → Deemix**. When enabled and selected as a sync target, every track in the playlist is searched on Deezer and queued for download automatically during import — no manual steps needed.

---

## MCP Integration (Claude Desktop / Claude Code)

Digarr ships an MCP server so you can drive it from chat — feed Claude a playlist link and tell it to import to Digarr, or ask whether you already have a song/artist.

It runs inside the Digarr container itself (no extra port, no API token) by importing the backend's functions directly, launched over stdio via `docker exec`. Add this to your MCP client config (e.g. `claude_desktop_config.json`) and restart the client:

```json
{
  "mcpServers": {
    "digarr": {
      "command": "docker",
      "args": ["exec", "-i", "digarr", "python", "-m", "mcp_server.server"]
    }
  }
}
```

Replace `digarr` in the `args` with your container name if you renamed it.

### Available tools

| Tool | Does |
|---|---|
| `list_playlists` | List every playlist Digarr has imported, with sync status per media server |
| `get_playlist` | Full detail for one playlist, including its track list |
| `parse_source` | Deterministically extract artists/tracks from a Spotify playlist URL or M3U content (not for arbitrary URLs/text — see below) |
| `import_playlist` | Create a playlist in Digarr: adds artists to Lidarr and pushes to Plex/Jellyfin/Navidrome/Spotify/Deemix |
| `get_import_status` | Poll a background job started by `import_playlist`/`replace_playlist`/`append_playlist` for progress and results |
| `replace_playlist` | Overwrite an existing playlist's artist/track list with fresh data you supply — re-syncs it in place instead of creating a duplicate |
| `append_playlist` | Add artists/tracks to an existing playlist without dropping what's already there |
| `refresh_playlist` | Re-fetch a playlist's source and add any new artists/tracks |
| `delete_playlist` | Permanently delete a playlist, including its pushed copy on any media server with the matching delete-on-remove setting enabled |
| `sync_playlist` / `sync_all` | Push one or every playlist to Plex, Jellyfin, or Navidrome |
| `search_library` | Check whether you already have a song/artist in your Plex/Jellyfin/Navidrome library |
| `lidarr_check_artists` | Check which artist names already exist in Lidarr |
| `lidarr_wanted` | Missing/wanted albums, restricted to artists Digarr has imported |

For a generic URL or pasted text (a blog post, a Reddit thread, a raw list), don't call `parse_source` — that routes through Digarr's own configured AI provider. Instead let Claude fetch/read the content and build the artist/track list itself, then hand it straight to `import_playlist`. `parse_source` is only for Spotify playlist links and M3U files, which Digarr parses deterministically without AI.

### Remote MCP endpoint (no `docker exec` access needed)

Besides the stdio server above, Digarr can also expose an OAuth-protected MCP endpoint over HTTPS, for MCP clients that can't `docker exec` into the host (e.g. Claude Desktop's remote "Custom Connector"). It's a separate process inside the same container, proxied at `/mcp` by the built-in nginx config.

It's opt-in: set these three environment variables on the container (all required together — leaving any unset disables the endpoint entirely, with no extra network exposure):

| Env var | Description |
|---|---|
| `DIGARR_MCP_ISSUER_URL` | OIDC issuer for a **dedicated** OAuth application/provider (Authentik, Keycloak, Authelia, etc.) — use a separate app from Digarr's own web login |
| `DIGARR_MCP_PUBLIC_URL` | The externally-reachable URL of this endpoint, e.g. `https://digarr.yourdomain.com/mcp` — must match exactly what's registered as the MCP server URL in the client |
| `DIGARR_MCP_CLIENT_ID` | Optional — only needed if your provider sets a stable audience per client (Authentik does not; leave unset for Authentik) |

Point the MCP client at `DIGARR_MCP_PUBLIC_URL`; it'll be walked through your OIDC provider's login/consent flow on first connect.

---

## Configuration

All config is stored in the Settings UI and persisted to `/data/config.json` inside the container.

| Setting | Description |
|---|---|
| Timezone | Timezone for displaying import/refresh timestamps |
| Anthropic API Key | Required for AI parsing when using Claude |
| OpenAI API Key | Required for AI parsing when using OpenAI |
| Active Provider | Which AI provider to use (Claude or OpenAI) |
| Model | Per-provider model selection |
| Lidarr URL | Full URL to your Lidarr instance |
| Lidarr API Key | Found in Lidarr → Settings → General |
| Quality Profile | Which Lidarr quality profile to use for new artists |
| Metadata Profile | Which Lidarr metadata profile to use |
| Root Folder | Where Lidarr should store music |
| Plex URL | Your Plex server URL (e.g. `http://192.168.1.x:32400`) |
| Plex Token | Your Plex auth token |
| Plex Library Section ID | The numeric ID of your Plex music library |
| Plex Sync Interval | How often to auto-sync all Plex playlists (off through weekly) |
| Append — Digarr to playlist names | Adds ` — Digarr` suffix to playlists in Plex (on by default) |
| Delete from Plex on remove | When a playlist is deleted from Digarr, also delete it from Plex |
| Jellyfin URL | Your Jellyfin server URL (e.g. `http://192.168.1.x:8096`) |
| Jellyfin API Key | Generate one in Jellyfin → Dashboard → API Keys |
| Jellyfin Sync Interval | How often to auto-sync all Jellyfin playlists (off through weekly) |
| Append — Digarr (Jellyfin) | Adds ` — Digarr` suffix to playlists in Jellyfin |
| Delete from Jellyfin on remove | When a playlist is deleted from Digarr, also delete it from Jellyfin |
| Navidrome URL | Your Navidrome server URL (e.g. `http://192.168.1.x:4533`) |
| Navidrome Username | Your Navidrome username |
| Navidrome Password | Your Navidrome password |
| Navidrome Sync Interval | How often to auto-sync all Navidrome playlists (off through weekly) |
| Append — Digarr (Navidrome) | Adds ` — Digarr` suffix to playlists in Navidrome |
| Delete from Navidrome on remove | When a playlist is deleted from Digarr, also delete it from Navidrome |
| Spotify Client ID / Secret | Required for all Spotify features. Create a free app at developer.spotify.com → Dashboard → Create App |
| Spotify OAuth Redirect URI | Must match what you register in your Spotify app. Set to `https://your-digarr-host/auth/spotify/callback`. After saving, click **Connect with Spotify** to authorize |
| ListenBrainz Username | Your ListenBrainz username — enables Weekly Jams, Daily Jams, and Weekly Exploration feeds on the Discover page |
| Last.fm API Key | Required for Similar to Library discovery. Get a free key at last.fm/api |
| Deemix URL | Your self-hosted Deemix instance URL (e.g. `http://192.168.1.x:6595`) — enables automatic Deezer queueing on import |
| Refresh Interval | How often to auto-refresh all playlists (off / 1h–bi-weekly) |
| Refresh Delay Between Playlists | Seconds to wait between each playlist during a scheduled refresh run, to avoid bursting Lidarr/media-server requests |
| Refresh Max New Artists | Caps how many net-new artists a single scheduled refresh run will add to Lidarr (0 = unlimited) |
| Webhook URL | Optional URL to POST a JSON summary after every scheduled refresh run |
| Webhook Only On Changes | When enabled, the webhook only fires for refresh runs that actually found new artists/tracks |
| Refresh Merge Tracks | When enabled, refreshes append new tracks instead of replacing the stored list |
| Password | Set a login password directly in Settings → General. Stored as a bcrypt hash in `config.json`. |
| OIDC Issuer / Client ID / Secret / Redirect URI | SSO via any OIDC provider (Authentik, Keycloak, etc.) — see [Authentication](#authentication) below |
| Allowed Emails | Optional comma-separated list of email addresses permitted to sign in via SSO. Leave blank to allow any valid account. |

> **Note:** Plex Pass is not required. Digarr uses standard Plex API endpoints available to all free accounts.

---

## Authentication

Digarr supports password login, SSO (OIDC), both, or neither.

### Password

Set a password in **Settings → General → Password**. It's stored as a bcrypt hash in `/data/config.json` — no environment variables needed.

To change it: enter a new password and click **Set Password**.  
To remove it: click **Clear password**.

> **Legacy:** The `DIGARR_PASSWORD` environment variable is still supported as a fallback. If both are set, the config-stored password takes precedence.

### SSO / OIDC (Authentik, Keycloak, etc.)

1. Create an OAuth2/OIDC application in your provider.
2. Set the redirect URI to `http://your-digarr-host/auth/oidc/callback`.
3. In Digarr **Settings → General → SSO / OIDC**, fill in:
   - **Issuer URL** — e.g. `https://auth.example.com/application/o/digarr/`
   - **Client ID** and **Client Secret**
   - **Redirect URI** — must match exactly what you registered
   - **Allowed Emails** — optional; restricts SSO login to specific addresses
4. Save. A **Sign in with SSO** button will appear on the login screen.

Password and OIDC can both be active simultaneously. Sessions expire after 30 days.

---

## Networking

### Direct access (no reverse proxy)

The default `docker compose up -d` exposes Digarr on `http://your-host:8090`. No extra networking config needed.

### Behind a reverse proxy

When Digarr and your reverse proxy run in **separate Docker Compose stacks**, they're on different Docker networks by default and can't reach each other by container name. You need to attach Digarr to the proxy's network.

**nginx-proxy-manager**

Find NPM's network name (usually `nginx-proxy-manager_default`):
```bash
docker network ls | grep nginx
```

Add it to your Digarr `docker-compose.yml`:
```yaml
services:
  digarr:
    image: digarr:latest
    build: .
    container_name: digarr
    ports:
      - "8090:8090"
    volumes:
      - digarr_data:/data
    networks:
      - digarr_internal
      - npm_network
    restart: unless-stopped

volumes:
  digarr_data:

networks:
  digarr_internal:
  npm_network:
    external: true
    name: nginx-proxy-manager_default
```

Then in NPM: proxy `digarr.yourdomain.com` → `http://digarr:8090`

**Traefik**

Attach Digarr to Traefik's network and add the standard labels:
```yaml
services:
  digarr:
    image: digarr:latest
    build: .
    container_name: digarr
    volumes:
      - digarr_data:/data
    networks:
      - traefik_network
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.digarr.rule=Host(`digarr.yourdomain.com`)"
      - "traefik.http.routers.digarr.entrypoints=websecure"
      - "traefik.http.routers.digarr.tls.certresolver=letsencrypt"
      - "traefik.http.services.digarr.loadbalancer.server.port=8090"
    restart: unless-stopped

volumes:
  digarr_data:

networks:
  traefik_network:
    external: true
    name: traefik_default
```

**Caddy**

No special network config needed if you use Caddy's Docker proxy plugin. Otherwise attach Digarr to Caddy's network the same way as NPM above, then add to your `Caddyfile`:
```
digarr.yourdomain.com {
    reverse_proxy digarr:8090
}
```

---

## Tech Stack

- **Backend**: FastAPI (Python)
- **Frontend**: React
- **Database**: SQLite
- **AI**: Anthropic Claude API / OpenAI API
- **Container**: Docker + nginx

---

## Security

### config.json

Sensitive values (API keys, tokens) are stored in `/data/config.json`. Digarr automatically sets this file to `600` (owner read/write only) on every write, so no manual chmod is needed.

### Env var overrides for secrets

You can supply any sensitive key via environment variable instead of storing it in `config.json`. Env vars take precedence over stored values:

| Env var | Overrides |
|---|---|
| `DIGARR_ANTHROPIC_KEY` | Anthropic API key |
| `DIGARR_OPENAI_KEY` | OpenAI API key |
| `DIGARR_LIDARR_KEY` | Lidarr API key |
| `DIGARR_PLEX_TOKEN` | Plex token |
| `DIGARR_SPOTIFY_CLIENT_ID` | Spotify client ID |
| `DIGARR_SPOTIFY_CLIENT_SECRET` | Spotify client secret |
| `DIGARR_LASTFM_KEY` | Last.fm API key |

This lets you use Docker `--env-file`, Compose `env_file:`, or a secrets manager without ever writing keys to disk.

### Non-root container

The container runs as a dedicated `digarr` user (non-root). If a vulnerability were exploited, the attacker would not have root access inside the container.

### Authentication

If Digarr is internet-facing, enable authentication in **Settings → General**. Without a password or OIDC configured, the app is open to anyone who can reach it.

### CORS

By default `allow_origins=["*"]` is set for ease of local use. On a public instance, restrict it:

```bash
docker run -e DIGARR_CORS_ORIGINS="https://digarr.yourdomain.com" ...
```

### Structured logging

By default logs are plain text. Set `LOG_FORMAT=json` to get newline-delimited JSON — useful when forwarding to a log aggregator (Loki, Datadog, etc.):

```bash
docker run -e LOG_FORMAT=json ...
```

Each line is a JSON object with `ts`, `level`, `logger`, `msg`, and (on errors) `exc`.

---

## Contributing

PRs welcome. This is a personal project built for the self-hosting community — if you use Lidarr and want a smarter way to feed it, this is for you.

---

## License

MIT

---

*Digarr is not affiliated with Lidarr, Anthropic, OpenAI, or any music service. It's just a tool built by someone who digs music.*
