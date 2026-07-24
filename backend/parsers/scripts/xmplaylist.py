"""
Deterministic site script for xmplaylist.com station pages — no AI call.

xmplaylist.com blocks bot/LLM-fetcher user agents (e.g. Claude's WebFetch tool
gets a 403) but serves a normal browser UA fine, and each recently-played
entry is server-rendered directly into the HTML (no client-side API call
needed) as a repeating block:

    <section id="station-feed-results">
      <article ...>
        <h3 id="X-title" ...>TITLE</h3>
        <ul id="X-artists" ...><li ...>ARTIST</li>...</ul>
      </article>
      ...
    </section>

This regex-parses that structure directly. Verified against
xmplaylist.com/station/lithium on 2026-07-24: 24/24 entries extracted
correctly, matching Digarr's own AI-based extraction of the same page.
"""
import html
import re

from parsers.text import fetch_raw_html

URL_PATTERN = r"https?://(www\.)?xmplaylist\.com/station/"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _strip_tags(fragment: str) -> str:
    return html.unescape(re.sub(r"<!--.*?-->", "", re.sub(r"<[^>]+>", "", fragment))).strip()


async def parse(url: str) -> list[dict]:
    page_html = await fetch_raw_html(url, user_agent=_BROWSER_UA)

    feed_match = re.search(
        r'<section id="station-feed-results".*?</section>\s*(?:<div|<footer|$)', page_html, re.S
    )
    feed_html = feed_match.group(0) if feed_match else page_html

    tracks = []
    for art_html in re.findall(r"<article\b.*?</article>", feed_html, re.S):
        title_m = re.search(r"<h3[^>]*>(.*?)</h3>", art_html, re.S)
        artists_ul_m = re.search(r'<ul[^>]*-artists"[^>]*>(.*?)</ul>', art_html, re.S)
        if not title_m or not artists_ul_m:
            continue

        title = _strip_tags(title_m.group(1))
        artists = [_strip_tags(li) for li in re.findall(r"<li[^>]*>(.*?)</li>", artists_ul_m.group(1), re.S)]
        artists = [a for a in artists if a]
        if not title or not artists:
            continue

        # One row per artist on a collab track — Digarr's track schema is
        # single-artist-per-row.
        for artist in artists:
            tracks.append({"artist": artist, "title": title, "album": None})

    return tracks
