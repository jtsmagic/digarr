"""
Site scripts: deterministic, no-AI parsers for specific sites, tried before
falling back to Digarr's configured AI provider for a "url" or "m3u_url"
source (same idea as the built-in Spotify/M3U special-casing, just
user-extensible).

Drop a .py file in this directory to add one. It must define:

    URL_PATTERN = r"regex matched against the playlist's source_url"

    async def parse(url: str) -> list[dict]:
        # Fetch/scrape `url` however this site needs (raw HTML, a JSON API,
        # etc.) and return a list of {"artist": str, "title": str,
        # "album": str | None} dicts — one per track. Return [] (not an
        # exception) if the site is unreachable/empty; the caller treats a
        # too-short result as a failure rather than wiping a playlist.

Files starting with "_" are ignored (e.g. this __init__.py). See
xmplaylist.py for a working example.
"""
import importlib
import logging
import os
import re

logger = logging.getLogger("parsers.scripts")

_DIR = os.path.dirname(__file__)


def _load_scripts():
    loaded = []
    for fname in sorted(os.listdir(_DIR)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        modname = fname[:-3]
        try:
            mod = importlib.import_module(f"parsers.scripts.{modname}")
            pattern = getattr(mod, "URL_PATTERN")
            parse_fn = getattr(mod, "parse")
            if not callable(parse_fn):
                raise TypeError("parse is not callable")
            loaded.append((modname, re.compile(pattern), mod))
        except Exception:
            logger.exception("Site script %r failed to load — skipping", modname)
    return loaded


_SCRIPTS = _load_scripts()


def find_script(url: str):
    """Return the first matching script module for `url`, or None."""
    if not url:
        return None
    for _name, pattern, mod in _SCRIPTS:
        if pattern.search(url):
            return mod
    return None
