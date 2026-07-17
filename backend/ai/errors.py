"""Shared error type for AI provider failures, with a user-facing message."""


class AIProviderError(Exception):
    """Raised when the configured AI provider fails to extract artists/tracks."""

    def __init__(self, provider: str, message: str, status_code: int = None):
        self.provider = provider
        self.status_code = status_code
        super().__init__(message)


def friendly_status_message(status_code: int, raw_message: str) -> str:
    """Turn an SDK status code into an actionable, human-readable message."""
    if status_code == 429:
        return (
            "Rate limited or out of quota. Check the provider's plan/billing, "
            "or switch AI provider in Settings."
        )
    if status_code == 401:
        return "Authentication failed — check the API key in Settings."
    if status_code == 403:
        return "Permission denied — check the API key's permissions/billing in Settings."
    if status_code and status_code >= 500:
        return f"The provider's API is having issues (HTTP {status_code}). Try again shortly."
    return f"API error (HTTP {status_code}): {raw_message}"
