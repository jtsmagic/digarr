import anthropic
import asyncio
import json
import re

SYSTEM_PROMPT = """You are a music data extraction assistant for Digarr, a tool that imports music into Lidarr.

Your job is to analyze any text content — web pages, blog posts, Reddit threads, playlist descriptions,
song lists, or any other format — and extract a structured list of artists and tracks.

Always respond with ONLY valid JSON in this exact format, no other text:
{
  "artists": [
    {"name": "Artist Name", "confidence": 95}
  ],
  "tracks": [
    {"artist": "Artist Name", "title": "Track Title", "album": "Album Name or null", "confidence": 90}
  ]
}

Rules:
- Extract every artist and track you can identify
- If you only see artist names with no track info, put them in artists and leave tracks empty
- If you see tracks, include the artist in both artists and tracks
- Deduplicate artists
- Ignore non-music content
- album can be null if not mentioned
- Clean up artist/track names (proper capitalization, remove feat. clutter if needed)
- If the content has no music data at all, return {"artists": [], "tracks": []}
- confidence is an integer 0-100 reflecting how certain you are this is a real artist/track:
  - 90-100: explicit, unambiguous music content
  - 70-89: likely correct but could be a misparse or alternate spelling
  - below 70: uncertain — may be wrong artist/track name or misidentified content
"""

class ClaudeProvider:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    async def extract_artists_and_tracks(self, content: str) -> dict:
        # Truncate very long content
        truncated = content[:15000] if len(content) > 15000 else content

        message = await asyncio.to_thread(
            self.client.messages.create,
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Extract all artists and tracks from this content:\n\n{truncated}"
                }
            ]
        )

        response_text = message.content[0].text.strip()

        # Strip markdown fences if present
        response_text = re.sub(r"^```json\s*", "", response_text)
        response_text = re.sub(r"^```\s*", "", response_text)
        response_text = re.sub(r"\s*```$", "", response_text)

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            data = {"artists": [], "tracks": []}

        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens

        return {
            "artists": data.get("artists", []),
            "tracks": data.get("tracks", []),
            "raw_source": content[:500],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "provider": "claude",
                "model": self.model,
            },
        }

