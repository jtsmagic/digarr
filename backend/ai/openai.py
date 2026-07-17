import asyncio
import json
import re
from openai import OpenAI, APIStatusError, APIConnectionError

from ai.claude import SYSTEM_PROMPT
from ai.errors import AIProviderError, friendly_status_message


class OpenAIProvider:
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    async def extract_artists_and_tracks(self, content: str) -> dict:
        truncated = content[:15000] if len(content) > 15000 else content

        try:
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
        except APIStatusError as e:
            raise AIProviderError("OpenAI", friendly_status_message(e.status_code, e.message)) from e
        except APIConnectionError as e:
            raise AIProviderError("OpenAI", f"Could not reach the OpenAI API: {e}") from e

        response_text = response.choices[0].message.content.strip()

        # Strip markdown fences if present
        response_text = re.sub(r"^```json\s*", "", response_text)
        response_text = re.sub(r"^```\s*", "", response_text)
        response_text = re.sub(r"\s*```$", "", response_text)

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            data = {"artists": [], "tracks": []}

        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens

        return {
            "artists": data.get("artists", []),
            "tracks": data.get("tracks", []),
            "raw_source": content[:500],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "provider": "openai",
                "model": self.model,
            },
        }
