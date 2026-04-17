import httpx
from bs4 import BeautifulSoup

async def fetch_url_content(url: str) -> str:
    """Fetch a URL and return cleaned text content."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Digarr/1.0; music playlist importer)"
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "")

        # Return raw text for M3U or plain text
        if "audio" in content_type or "plain" in content_type or url.endswith(".m3u") or url.endswith(".m3u8"):
            return r.text

        # Parse HTML and extract text
        soup = BeautifulSoup(r.text, "html.parser")

        # Remove scripts, styles, nav, footer
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)

        # Clean up whitespace
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

