"""
Generic OAuth 2.0 access-token verifier for any standards-compliant OIDC provider
(Authentik, Keycloak, Authelia, etc.) — used by http_server.py to authenticate
remote MCP clients (e.g. Claude Desktop's "Custom Connector" OAuth flow).

Discovers the provider's JWKS endpoint via the standard
`{issuer}/.well-known/openid-configuration` document (OIDC Discovery 1.0), then
verifies each bearer token's signature, issuer, expiry, and (if configured)
audience locally — no callback to the identity provider on every request.
"""
import logging

import httpx
import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

logger = logging.getLogger("mcp_server.oidc_verifier")

# Some providers sit behind bot-protection (e.g. Cloudflare) that blocks
# generic HTTP client User-Agents like Python's default urllib/httpx strings.
# PyJWKClient uses urllib internally, so give it a normal-looking UA.
_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Digarr-MCP/1.0)"}


def discover_jwks_uri(issuer_url: str) -> str:
    """
    Fetch {issuer}/.well-known/openid-configuration and return its jwks_uri.
    Per OIDC Discovery 1.0: any trailing slash on the issuer is stripped before
    appending the well-known suffix.
    """
    discovery_url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    resp = httpx.get(discovery_url, headers=_HTTP_HEADERS, timeout=10.0)
    resp.raise_for_status()
    jwks_uri = resp.json().get("jwks_uri")
    if not jwks_uri:
        raise ValueError(f"No jwks_uri in OIDC discovery document at {discovery_url}")
    return jwks_uri


class OIDCTokenVerifier(TokenVerifier):
    """
    Verifies bearer tokens as RS256-signed JWTs issued by `issuer_url`.

    `audience` is optional: some providers (e.g. Authentik) don't support
    RFC 8707 resource indicators and always set `aud` to the requesting
    client's ID, so a dedicated OAuth application/provider per protected
    resource is the practical isolation mechanism — the issuer itself (scoped
    to that one application) already restricts which tokens are accepted.
    Pass `audience` when the provider does support per-resource audiences,
    for defense in depth.
    """

    def __init__(self, issuer_url: str, jwks_uri: str, audience: str | None = None):
        self._issuer = issuer_url
        self._audience = audience
        self._jwks_client = jwt.PyJWKClient(jwks_uri, headers=_HTTP_HEADERS)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"verify_aud": self._audience is not None},
            )
        except jwt.PyJWTError as e:
            try:
                unverified = jwt.decode(token, options={"verify_signature": False})
                logger.warning(
                    "verify_token: rejected — %r (expected issuer=%r, token iss=%r aud=%r)",
                    e, self._issuer, unverified.get("iss"), unverified.get("aud"),
                )
            except Exception:
                logger.warning("verify_token: rejected — %r (expected issuer=%r)", e, self._issuer)
            return None

        scopes = claims.get("scope", "")
        if isinstance(scopes, str):
            scopes = scopes.split()

        aud = claims.get("aud")
        if isinstance(aud, list):
            aud = aud[0] if aud else None
        client_id = aud or claims.get("sub") or "unknown"

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=claims.get("exp"),
        )
