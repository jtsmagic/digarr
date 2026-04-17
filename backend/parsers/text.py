import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

_MAX_CONTENT_BYTES = 5 * 1024 * 1024  # 5 MB


def _assert_safe_url(url: str) -> None:
    """Raise ValueError if the URL targets a private/internal address or uses a non-http(s) scheme.

    Resolves the hostname to an IP before checking — prevents bypasses via e.g. 0x7f000001.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError("Invalid URL")

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme '{parsed.scheme}' is not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")

    try:
        ip_str = socket.gethostbyname(hostname)
        addr = ipaddress.ip_address(ip_str)
    except Exception:
        raise ValueError(f"Could not resolve hostname: {hostname}")

    if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved or addr.is_multicast:
        raise ValueError("URL resolves to a private or internal address")


async def fetch_url_content(url: str) -> str:
    """Fetch a URL and return cleaned text content."""
    _assert_safe_url(url)

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Digarr/1.0; music playlist importer)"
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            content_type = r.headers.get("content-type", "")

            chunks = []
            total = 0
            async for chunk in r.aiter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > _MAX_CONTENT_BYTES:
                    raise ValueError("Response too large (limit: 5 MB)")
                chunks.append(chunk)

            raw = b"".join(chunks).decode("utf-8", errors="ignore")

        # Return raw text for M3U or plain text
        if "audio" in content_type or "plain" in content_type or url.endswith(".m3u") or url.endswith(".m3u8"):
            return raw

        # Parse HTML and extract text
        soup = BeautifulSoup(raw, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)
