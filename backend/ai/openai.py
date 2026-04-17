import asyncio
import json
import re
from openai import OpenAI

from ai.claude import SYSTEM_PROMPT


class OpenAIProvider:
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    async def extract_artists_and_tracks(self, content: str) -> dict:
        truncated = content[:15000] if len(content) > 15000 else content

        response = await asyncio.to_thread(
            self.client.chat.completions.create,
            model=self.model,
            max_tokens=4096,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Extract all artists and tracks from this content:\n\n{truncated}"},
            ],
        )

        response_text = response.choices[0].message.content.strip()

        # Strip markdown fences if present
        response_text = re.sub(r"^```json\s*", "", response_text)
        response_text = re.sub(r"^```\s*", "", response_text)
        response_text = re.sub(r"\s*```$", "", response_text)

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            data = {"artists": [], "tracks": []}

        return {
            "artists": data.get("artists", []),
            "tracks": data.get("tracks", []),
            "raw_source": content[:500],
        }
