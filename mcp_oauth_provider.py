"""OAuth 2.1 authorization server for ``investyo_mcp_server.py``'s
``streamable-http`` transport.

Why this exists: claude.ai's custom-connector UI has no static-bearer-token
field, only a "connect" button that drives a full OAuth 2.1 flow (RFC 7591
dynamic client registration, RFC 7636 PKCE, the standard authorization-code
+ refresh-token grant). ``investyo_mcp_server.py --transport streamable-http
--auth-mode oauth`` (gated by ``settings.MCP_OAUTH_ENABLED``) wires this
module in as the MCP SDK's ``OAuthAuthorizationServerProvider``, so a
claude.ai connection negotiates real OAuth instead of a static bearer token.

RFC 7591 dynamic client registration is unauthenticated by design -- any
client can self-register. That means registration alone grants no access;
the actual trust boundary is the human-facing ``/login`` password form
(``settings.MCP_OAUTH_PASSWORD``) that ``authorize()`` redirects to before an
authorization code is ever minted. A client that never gets a human past
that form never gets a code, and never gets a token.

All state (registered clients, pending authorizations, issued codes and
tokens, login lockout) is durable via ``mcp_oauth_store.McpOAuthStore`` --
this module holds no in-memory state of its own, so a server restart mid-flow
loses nothing but an in-flight, still-reloadable pending authorization or
code (both self-expire on their own TTL regardless).
"""

from __future__ import annotations

import hmac
import html
import secrets
import time
from typing import Optional

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mcp_oauth_store import (
    ACCESS_TOKEN_TTL_SECONDS,
    AUTH_CODE_TTL_SECONDS,
    REFRESH_TOKEN_TTL_SECONDS,
    McpOAuthStore,
)
from settings import settings

# ---------------------------------------------------------------------------
# Small HTML templates for the /login form. All server-generated values are
# still passed through html.escape() before interpolation -- defense in
# depth, cheap, even though the nonce is never user-influenced.
# ---------------------------------------------------------------------------

_LOGIN_FORM_TEMPLATE = """<!doctype html>
<html>
<head><title>InvestYo MCP — Sign in</title></head>
<body>
<h1>InvestYo MCP</h1>
{error_banner}
{locked_banner}
<form method="post" action="/login?req={nonce}">
  <input type="hidden" name="req" value="{nonce}">
  <label for="password">Password</label>
  <input type="password" id="password" name="password" autofocus>
  <button type="submit">Sign in</button>
</form>
</body>
</html>
"""

_EXPIRED_TEMPLATE = """<!doctype html>
<html>
<head><title>InvestYo MCP — Link expired</title></head>
<body>
<h1>This sign-in link has expired</h1>
<p>Please restart the connection from your MCP client and try again.</p>
</body>
</html>
"""

_ERROR_BANNER = '<p style="color:red">Incorrect password. Please try again.</p>'
_LOCKED_BANNER = (
    '<p style="color:red">Too many failed attempts. This sign-in is temporarily '
    "locked — please wait before trying again.</p>"
)


def _render_login_form(nonce: str, *, error: bool = False, locked: bool = False) -> str:
    safe_nonce = html.escape(nonce)
    return _LOGIN_FORM_TEMPLATE.format(
        nonce=safe_nonce,
        error_banner=_ERROR_BANNER if error else "",
        locked_banner=_LOCKED_BANNER if locked else "",
    )


class InvestyoOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """``OAuthAuthorizationServerProvider`` implementation backed by
    ``mcp_oauth_store.McpOAuthStore``.

    Two confirmed deviations from a literal reading of the RFC/SDK spec
    paraphrase (both verified against the installed ``mcp==1.28.1`` source,
    not assumed):

    1. ``revoke_token`` receives the token OBJECT (``AccessToken |
       RefreshToken``) at the Protocol level, not a bare string -- this
       class unwraps ``token.token`` before delegating to the store's
       string-based ``McpOAuthStore.revoke_token(token: str)``.
    2. ``AccessToken.expires_at`` / ``RefreshToken.expires_at`` are typed
       ``int | None`` (not ``float``) -- constructing either with a raw
       float ``expires_at`` raises a pydantic validation error. This class
       always does ``int(...)`` when building those two model instances
       from the store's float timestamps. ``AuthorizationCode.expires_at``
       *is* ``float`` -- no cast needed there.
    """

    def __init__(self, store: Optional[McpOAuthStore] = None) -> None:
        self.store = store or McpOAuthStore()

    # ------------------------------------------------------------------
    # Client registration
    # ------------------------------------------------------------------

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        row = self.store.get_client(client_id)
        if row is None:
            return None
        return self._client_row_to_model(row)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.store.register_client(self._client_model_to_row(client_info))

    @staticmethod
    def _client_row_to_model(row: dict) -> OAuthClientInformationFull:
        kwargs = dict(row)
        kwargs.pop("created_at", None)
        return OAuthClientInformationFull(**kwargs)

    @staticmethod
    def _client_model_to_row(client_info: OAuthClientInformationFull) -> dict:
        data = client_info.model_dump(mode="json")
        data["created_at"] = time.time()
        return data

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        nonce = secrets.token_urlsafe(24)
        self.store.save_pending_authorization(
            nonce,
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "state": params.state,
                "scopes": params.scopes,
                "code_challenge": params.code_challenge,
                "resource": params.resource,
                "expires_at": time.time() + 600,
            },
        )
        return f"/login?req={nonce}"

    # ------------------------------------------------------------------
    # Authorization codes
    # ------------------------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        row = self.store.load_authorization_code(authorization_code)
        if row is None or row["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=row["code"],
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=row["redirect_uri"],
            redirect_uri_provided_explicitly=row["redirect_uri_provided_explicitly"],
            resource=row.get("resource"),
            subject=row.get("subject"),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self.store.delete_authorization_code(authorization_code.code)

        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        now = time.time()
        access_expires_at = now + ACCESS_TOKEN_TTL_SECONDS
        refresh_expires_at = now + REFRESH_TOKEN_TTL_SECONDS

        self.store.save_access_token(
            access_token,
            {
                "client_id": client.client_id,
                "scopes": authorization_code.scopes,
                "resource": authorization_code.resource,
                "subject": authorization_code.subject,
                "expires_at": access_expires_at,
            },
        )
        self.store.save_refresh_token(
            refresh_token,
            {
                "client_id": client.client_id,
                "scopes": authorization_code.scopes,
                "subject": authorization_code.subject,
                "expires_at": refresh_expires_at,
            },
        )

        scopes = authorization_code.scopes
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=refresh_token,
        )

    # ------------------------------------------------------------------
    # Refresh tokens
    # ------------------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        row = self.store.load_refresh_token(refresh_token)
        if row is None or row["client_id"] != client.client_id:
            return None
        expires_at = row.get("expires_at")
        return RefreshToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=int(expires_at) if expires_at is not None else None,
            subject=row.get("subject"),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list,
    ) -> OAuthToken:
        # Rotate: delete the old refresh token, mint a brand-new access +
        # refresh pair. The old refresh token becomes unloadable immediately
        # (reuse-detection-friendly, not merely "still works until it
        # expires").
        self.store.delete_refresh_token(refresh_token.token)

        new_scopes = scopes if scopes else refresh_token.scopes

        new_access_token = secrets.token_urlsafe(32)
        new_refresh_token = secrets.token_urlsafe(32)
        now = time.time()
        access_expires_at = now + ACCESS_TOKEN_TTL_SECONDS
        refresh_expires_at = now + REFRESH_TOKEN_TTL_SECONDS

        self.store.save_access_token(
            new_access_token,
            {
                "client_id": client.client_id,
                "scopes": new_scopes,
                "subject": refresh_token.subject,
                "expires_at": access_expires_at,
            },
        )
        self.store.save_refresh_token(
            new_refresh_token,
            {
                "client_id": client.client_id,
                "scopes": new_scopes,
                "subject": refresh_token.subject,
                "expires_at": refresh_expires_at,
            },
        )

        return OAuthToken(
            access_token=new_access_token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(new_scopes) if new_scopes else None,
            refresh_token=new_refresh_token,
        )

    # ------------------------------------------------------------------
    # Access tokens
    # ------------------------------------------------------------------

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        row = self.store.load_access_token(token)
        if row is None:
            return None
        expires_at = row.get("expires_at")
        return AccessToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=int(expires_at) if expires_at is not None else None,
            resource=row.get("resource"),
            subject=row.get("subject"),
            claims=row.get("claims"),
        )

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------

    async def revoke_token(self, token) -> None:  # noqa: ANN001 - AccessToken | RefreshToken
        self.store.revoke_token(token.token)


# ---------------------------------------------------------------------------
# /login routes
# ---------------------------------------------------------------------------


def register_login_routes(mcp: FastMCP, provider: InvestyoOAuthProvider) -> None:
    """Registers the human-facing ``GET``/``POST /login`` routes onto ``mcp``.

    Two separate ``@mcp.custom_route`` registrations at the same path with
    disjoint ``methods`` -- verified to route correctly in Starlette (a GET
    request matches the GET route directly; a POST falls through to the POST
    route). ``FastMCP.streamable_http_app()`` always extends its route list
    with every registered custom route, regardless of whether ``auth=`` was
    configured on the ``FastMCP(...)`` constructor, so this works standalone.
    """

    @mcp.custom_route("/login", methods=["GET"])
    async def login_get(request: Request) -> Response:  # noqa: ANN001
        nonce = request.query_params.get("req", "")
        pending = provider.store.load_pending_authorization(nonce)
        if pending is None:
            return HTMLResponse(_EXPIRED_TEMPLATE, status_code=404)

        error = request.query_params.get("error") == "1"
        return HTMLResponse(_render_login_form(nonce, error=error))

    @mcp.custom_route("/login", methods=["POST"])
    async def login_post(request: Request) -> Response:  # noqa: ANN001
        form = await request.form()
        nonce = str(form.get("req", "") or request.query_params.get("req", ""))

        pending = provider.store.load_pending_authorization(nonce)
        if pending is None:
            return HTMLResponse(_EXPIRED_TEMPLATE, status_code=404)

        if provider.store.is_locked_out():
            return HTMLResponse(_render_login_form(nonce, locked=True), status_code=429)

        expected_password = settings.MCP_OAUTH_PASSWORD
        if not expected_password:
            raise RuntimeError(
                "MCP_OAUTH_PASSWORD is not set — refusing to treat an unset "
                "password as 'anything passes'."
            )

        submitted_password = str(form.get("password", "") or "")
        if not hmac.compare_digest(submitted_password, expected_password):
            provider.store.record_login_failure()
            return HTMLResponse(_render_login_form(nonce, error=True))

        provider.store.reset_login_state()

        # Re-check expiry after the (human, possibly slow) form submission.
        pending = provider.store.load_pending_authorization(nonce)
        if pending is None:
            return HTMLResponse(_EXPIRED_TEMPLATE, status_code=404)

        code = secrets.token_urlsafe(32)
        provider.store.save_authorization_code(
            code,
            {
                "client_id": pending["client_id"],
                "redirect_uri": pending["redirect_uri"],
                "redirect_uri_provided_explicitly": pending["redirect_uri_provided_explicitly"],
                "scopes": pending.get("scopes"),
                "code_challenge": pending["code_challenge"],
                "resource": pending.get("resource"),
                "expires_at": time.time() + AUTH_CODE_TTL_SECONDS,
            },
        )
        provider.store.delete_pending_authorization(nonce)

        redirect_uri = construct_redirect_uri(
            pending["redirect_uri"], code=code, state=pending.get("state")
        )
        return RedirectResponse(url=redirect_uri, status_code=302)
