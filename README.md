# Digarr ⦿

**The crates don't fill themselves.**

Digarr is a self-hosted web app that imports artists and playlists into [Lidarr](https://lidarr.audio) using AI to parse any source — blog posts, Reddit threads, M3U files, URLs, raw text lists, and more.

![Digarr](https://img.shields.io/badge/arr-ecosystem-orange?style=flat-square) ![Docker](https://img.shields.io/badge/docker-ready-blue?style=flat-square) ![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-yellow?style=flat-square&logo=buy-me-a-coffee)](https://www.buymeacoffee.com/jtsmagic)

---

## Features

- **Parse anything** — paste a URL, upload an M3U, drop in a raw text list, or use a Spotify playlist URL; M3U URLs are auto-detected, no separate tab needed
- **AI-powered extraction** — Claude identifies artists and tracks from unstructured content; confidence scores dim low-confidence results so you can review before adding
- **Lidarr integration** — search, check library, and add artists in one click
- **Track status** — see which tracks are downloaded (green/yellow/red) vs missing; unmatched tracks can be ignored or manually matched
- **Manual track matching** — search your Plex library cache for unmatched tracks and confirm the right match; matches persist across refreshes
- **M3U / JSPF export** — download parsed playlists as M3U or JSON (JSPF) files
- **Plex integration** — push playlists directly to Plex; unmatched tracks trigger Lidarr monitoring automatically; Sync All button re-syncs every Plex playlist in one shot
- **Playlist history** — every import saved locally with full track/artist detail; inline rename syncs to Plex atomically
- **Background import queue** — imports run in the background with a live progress bar; navigate away and come back without losing state
- **Playlist refresh** — re-fetch any source URL, add net-new artists to Lidarr, and re-sync Plex in one shot; merge mode appends new tracks rather than replacing
- **Scheduled refresh** — auto-refresh all playlists on a configurable interval (6h / 12h / daily / weekly); per-playlist include/exclude control; webhook fires after each run
- **Wanted/missing report** — see which Lidarr artists added via Digarr still have undownloaded albums
- **Discover page** — curated feeds from Spotify, ListenBrainz, Similar to Library, and Discogs Wantlist; review recommendations with library status badges and import directly to Lidarr/Plex
- **Spotify OAuth** — connect your Spotify account to import Discover Weekly, Daily Mixes, Release Radar, and Liked Songs; editorial playlists that previously required URL workarounds just work; PKCE flow, tokens auto-refresh
- **Plex → Spotify sync** — push any Digarr playlist to Spotify from the History menu; creates or updates the Spotify playlist; match count shown inline
- **ListenBrainz recommendations** — Weekly Jams, Daily Jams, and Weekly Exploration feeds pulled from your ListenBrainz account; tracks you haven't been listening to, not just what's already owned
- **Similar to Library** — uses your Last.fm API key to find artists similar to up to 75 randomly sampled artists in your Lidarr library; ranked by frequency across matches, owned artists filtered out
- **Discogs Wantlist** — pulls every release from your Discogs wantlist via personal access token; one-click add artists to Lidarr
- **Clean web UI** — dark, vinyl-inspired interface
- **Multi-AI support** — Claude (Haiku/Sonnet/Opus) and OpenAI (GPT-4o mini/GPT-4o), switchable from Settings with per-provider model selection

---

## Quick Start

### Requirements
- Docker + Docker Compose
- Lidarr instance
- Anthropic API key ([get one here](https://console.anthropic.com))

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

### Import from M3U file
Upload an M3U file or drag-and-drop it. Digarr parses the `#EXTINF` tags directly, no AI needed.

### Import from text
Paste a raw list of artists or songs. Claude will figure out the structure.

### Add to Lidarr
After parsing, you get a table of artists with checkboxes. Select the ones you want, hit **Add to Lidarr**, and Digarr will:
1. Check if the artist already exists in your library
2. Search MusicBrainz for the artist
3. Add them with your configured quality/metadata profile

### Export M3U
Any parsed playlist can be downloaded as an M3U file for use in other players.

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
| Spotify Client ID / Secret | Required for all Spotify features. Create a free app at developer.spotify.com → Dashboard → Create App |
| Spotify OAuth Redirect URI | Must match what you register in your Spotify app. Set to `https://your-digarr-host/auth/spotify/callback`. After saving, click **Connect with Spotify** to authorize |
| ListenBrainz Username | Your ListenBrainz username — enables Weekly Jams, Daily Jams, and Weekly Exploration feeds on the Discover page |
| Last.fm API Key | Required for Similar to Library discovery. Get a free key at last.fm/api |
| Discogs Username | Your Discogs username — required for Wantlist import on the Discover page |
| Discogs Token | Your Discogs personal access token — generate one in Discogs → Settings → Developer |
| Plex URL | Your Plex server URL (e.g. `http://192.168.1.x:32400`) |
| Plex Token | Your Plex auth token |
| Plex Library Section ID | The numeric ID of your Plex music library |
| Append — Digarr to playlist names | Adds ` — Digarr` suffix to playlists created in Plex (on by default) |
| Delete from Plex on remove | When a playlist is deleted from Digarr, also delete it from Plex (off by default) |
| Refresh Interval | How often to auto-refresh all playlists (off / 6h / 12h / daily / weekly) |
| Webhook URL | Optional URL to POST a JSON summary after every scheduled refresh run |
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

## What's been built

- **Import anything** — URL, M3U file, raw text paste, or Spotify playlist (your own playlists and Liked Songs via the Spotify tab)
- **AI-powered parsing** — Claude or OpenAI extracts artists and tracks from unstructured content; confidence scores flag uncertain results
- **Lidarr integration** — search, check library status, and add artists directly
- **Track status** — green/yellow/red indicators show what's downloaded, monitored, or missing
- **Manual track matching** — search your Plex library cache for unmatched tracks; matches persist across refreshes
- **M3U / JSPF export** — download any parsed playlist in either format
- **Plex integration** — push playlists to Plex; unmatched tracks trigger Lidarr monitoring; Sync All re-syncs everything in one shot; optional ` — Digarr` suffix; optional delete-on-remove
- **Spotify integration** — OAuth (PKCE) for user playlists and Liked Songs; push any Digarr playlist to Spotify from History; two-way sync
- **Playlist history** — every import saved with full track/artist detail; inline rename syncs to Plex atomically
- **Background imports** — jobs run in the background with a live progress bar; navigate away without losing state
- **Playlist refresh** — re-fetch any source URL, add net-new artists, re-sync Plex; merge mode appends instead of replacing
- **Scheduled refresh** — auto-refresh on a configurable interval (6h / 12h / daily / weekly); per-playlist include/exclude; webhook fires after each run
- **Discover page** — ListenBrainz recommendation feeds (Weekly Jams, Daily Jams, Weekly Exploration), Similar to Library via Last.fm, and Discogs Wantlist; all with library status badges and direct Lidarr/Plex import
- **Wanted/missing report** — see which Lidarr artists added via Digarr still have undownloaded albums
- **Artist blocklist** — permanently ignore specific artists across all imports and refreshes
- **Authentication** — password login (bcrypt) and/or OIDC SSO (Authentik, Keycloak, etc.); both can be active simultaneously; 30-day sessions
- **Rate limiting + input validation** — hardened for self-hosted, internet-facing use
- **Multi-AI support** — Claude and OpenAI, switchable from Settings with per-provider model selection
- **First-run onboarding** — setup checklist shown on Import until AI provider and Lidarr are configured

---

## Features we're working on

- **Jellyfin support** — playlist push, sync, track cache, and media server selector (Plex / Jellyfin / both)
- **Downloader integration** — direct Deemix API support for queueing downloads outside Lidarr
- **Shareable import links** — generate a link someone else can paste into their own Digarr instance

---

## Tech Stack

- **Backend**: FastAPI (Python)
- **Frontend**: React
- **Database**: SQLite
- **AI**: Anthropic Claude API / OpenAI API
- **Container**: Docker + nginx

---

## Contributing

PRs welcome. This is a personal project built for the self-hosting community — if you use Lidarr and want a smarter way to feed it, this is for you.

---

## License

MIT

---

*Digarr is not affiliated with Lidarr, Anthropic, OpenAI, or any music service. It's just a tool built by someone who digs music.*
