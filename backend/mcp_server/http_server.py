"""
Remote, OAuth-protected MCP endpoint — lets any MCP client (e.g. Claude Desktop's
"Custom Connector") reach Digarr over HTTPS instead of a local docker exec.

Runs as its OWN standalone uvicorn process, served at its ASGI root — NOT
mounted inside main.py's app. FastMCP's RFC 9728 well-known discovery route is
computed as a fixed path derived from `resource_server_url`; sub-mounting it
under a path prefix (e.g. app.mount("/mcp", ...)) silently breaks that
discovery (double-prefixes the well-known path), so it must be the top-level
app nginx proxies straight through to.

`stateless_http=True` is required because the container runs multiple uvicorn
worker processes behind nginx with no session affinity — this transport mode
treats every request as self-contained instead of relying on server-held
session state tied to one worker.

Only starts (see start.sh) when DIGARR_MCP_ISSUER_URL and DIGARR_MCP_PUBLIC_URL
are both set — unconfigured installs get no new network exposure at all.

Env vars:
    DIGARR_MCP_ISSUER_URL   OIDC issuer for a DEDICATED OAuth application/provider
                            (Authentik/Keycloak/Authelia/etc.) — not the same
                            client used for Digarr's own web login.
    DIGARR_MCP_PUBLIC_URL   The externally-reachable URL of this endpoint, e.g.
                            https://digarr.example.com/mcp — must match exactly
                            what's registered as the MCP server URL in the client.
    DIGARR_MCP_CLIENT_ID    Optional — if the provider sets a stable audience,
                            validate it. Omit for providers (e.g. Authentik)
                            that always set aud=requesting-client-id.
    MCP_PORT                Internal port to bind (default 8092); must match
                            what nginx.conf proxies to.

Launch (from the container, CWD=/app):
    python -m mcp_server.http_server
"""
import os
import sys

import uvicorn
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from mcp_server import tools
from mcp_server.oidc_verifier import OIDCTokenVerifier, discover_jwks_uri


def main() -> None:
    issuer_url = os.environ.get("DIGARR_MCP_ISSUER_URL", "").strip()
    public_url = os.environ.get("DIGARR_MCP_PUBLIC_URL", "").strip()
    client_id = os.environ.get("DIGARR_MCP_CLIENT_ID", "").strip() or None
    port = int(os.environ.get("MCP_PORT", "8092"))

    if not issuer_url or not public_url:
        print(
            "mcp_server.http_server: DIGARR_MCP_ISSUER_URL and DIGARR_MCP_PUBLIC_URL "
            "must both be set. Exiting.",
            file=sys.stderr,
        )
        sys.exit(1)

    jwks_uri = discover_jwks_uri(issuer_url)
    verifier = OIDCTokenVerifier(issuer_url=issuer_url, jwks_uri=jwks_uri, audience=client_id)

    mcp = FastMCP(
        "digarr",
        stateless_http=True,
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=issuer_url,
            resource_server_url=public_url,
            required_scopes=[],
        ),
    )
    for fn in tools.ALL_TOOLS:
        mcp.tool()(fn)

    uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
