"""SQLAlchemy-backed persistence for the InvestYo MCP OAuth 2.1 authorization
server (``mcp_oauth_provider.py``).

Pure data layer: no MCP SDK imports, no pydantic, no ``settings`` import --
this module is a dependency-light, independently-testable leaf, matching the
``data/paper_account_store.py`` / ``sizing/cap_audit_store.py`` convention
(own ``Base = declarative_base()``, one ``class Foo(Base)`` per table, a
store class with ``__init__(self, db_url=None)`` -> ``create_db_engine`` ->
``Base.metadata.create_all`` -> ``sessionmaker`` -> ``session_scope`` for
every write).

Backs six concerns of a minimal OAuth 2.1 authorization server:

- ``oauth_clients``: RFC 7591 dynamic client registration records.
- ``oauth_pending_authorizations``: the short-lived nonce created by
  ``authorize()`` while the human completes the ``/login`` form.
- ``oauth_authorization_codes``: single-use RFC 6749 authorization codes.
- ``oauth_access_tokens`` / ``oauth_refresh_tokens``: issued token pairs.
- ``oauth_login_state``: a singleton row (``id=1``) tracking consecutive
  ``/login`` password failures for a simple lockout, mirroring
  ``data/paper_account_store.py``'s ``PaperAccount.id==1`` singleton-row
  pattern.

All JSON-shaped columns (``redirect_uris``, ``grant_types``,
``response_types``, ``contacts``, ``jwks``, ``scopes``, ``claims``) are
stored as ``Column(Text)`` with manual ``json.dumps``/``json.loads`` at the
store boundary -- mirrors this codebase's ``raw_json`` TEXT-column
convention (e.g. ``data/historical_store.py``) rather than a backend-specific
``sqlalchemy.JSON`` type, so this is portable to Postgres/SQLite identically.

Every ``load_*`` method self-checks expiry inline (``expires_at is not None
and expires_at < time.time()`` -> treat the row as absent, return ``None``).
No lazy delete-on-read is performed by those loaders -- correctness never
depends on ``purge_expired()`` running; it exists purely for housekeeping
(keeping the tables from growing unbounded).

``time`` is imported as a module (``import time`` + ``time.time()`` calls
throughout, never ``from time import time``) specifically so tests can
``monkeypatch.setattr(mcp_oauth_store.time, "time", ...)`` to simulate lockout
expiry without real wall-clock sleeps.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Column, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

AUTH_CODE_TTL_SECONDS = 120
PENDING_AUTHZ_TTL_SECONDS = 600
ACCESS_TOKEN_TTL_SECONDS = 3600
REFRESH_TOKEN_TTL_SECONDS = 90 * 24 * 3600
LOGIN_LOCKOUT_THRESHOLD = 5
LOGIN_LOCKOUT_SECONDS = 900

Base = declarative_base()


class OAuthClient(Base):
    __tablename__ = "oauth_clients"

    client_id = Column(String(255), primary_key=True)
    client_secret = Column(String(255), nullable=True)
    client_id_issued_at = Column(Float, nullable=True)
    client_secret_expires_at = Column(Float, nullable=True)
    redirect_uris = Column(Text, nullable=False)  # JSON list[str]
    token_endpoint_auth_method = Column(String(64), nullable=True)
    grant_types = Column(Text, nullable=True)  # JSON list[str]
    response_types = Column(Text, nullable=True)  # JSON list[str]
    scope = Column(Text, nullable=True)
    client_name = Column(String(255), nullable=True)
    client_uri = Column(String(1024), nullable=True)
    logo_uri = Column(String(1024), nullable=True)
    contacts = Column(Text, nullable=True)  # JSON list[str]
    tos_uri = Column(String(1024), nullable=True)
    policy_uri = Column(String(1024), nullable=True)
    jwks_uri = Column(String(1024), nullable=True)
    jwks = Column(Text, nullable=True)  # JSON
    software_id = Column(String(255), nullable=True)
    software_version = Column(String(255), nullable=True)
    created_at = Column(Float, nullable=False)


class OAuthPendingAuthorization(Base):
    __tablename__ = "oauth_pending_authorizations"

    nonce = Column(String(64), primary_key=True)
    client_id = Column(String(255), nullable=False)
    redirect_uri = Column(String(2048), nullable=False)
    redirect_uri_provided_explicitly = Column(Boolean, nullable=False, default=False)
    state = Column(String(1024), nullable=True)
    scopes = Column(Text, nullable=True)  # JSON list[str]
    code_challenge = Column(String(512), nullable=False)
    resource = Column(String(1024), nullable=True)
    created_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=False)


class OAuthAuthorizationCode(Base):
    __tablename__ = "oauth_authorization_codes"

    code = Column(String(255), primary_key=True)
    client_id = Column(String(255), nullable=False)
    redirect_uri = Column(String(2048), nullable=False)
    redirect_uri_provided_explicitly = Column(Boolean, nullable=False, default=False)
    scopes = Column(Text, nullable=True)  # JSON list[str]
    code_challenge = Column(String(512), nullable=False)
    resource = Column(String(1024), nullable=True)
    subject = Column(String(255), nullable=True)
    created_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=False)


class OAuthAccessToken(Base):
    __tablename__ = "oauth_access_tokens"

    token = Column(String(255), primary_key=True)
    client_id = Column(String(255), nullable=False)
    scopes = Column(Text, nullable=True)  # JSON list[str]
    resource = Column(String(1024), nullable=True)
    subject = Column(String(255), nullable=True)
    claims = Column(Text, nullable=True)  # JSON dict
    created_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=True)


class OAuthRefreshToken(Base):
    __tablename__ = "oauth_refresh_tokens"

    token = Column(String(255), primary_key=True)
    client_id = Column(String(255), nullable=False)
    scopes = Column(Text, nullable=True)  # JSON list[str]
    subject = Column(String(255), nullable=True)
    created_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=True)


class OAuthLoginState(Base):
    __tablename__ = "oauth_login_state"

    id = Column(Integer, primary_key=True)  # always 1
    fail_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(Float, nullable=True)


def _dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value)


def _loads(value: Optional[str]) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _client_row_to_dict(row: OAuthClient) -> Dict[str, Any]:
    return {
        "client_id": row.client_id,
        "client_secret": row.client_secret,
        "client_id_issued_at": row.client_id_issued_at,
        "client_secret_expires_at": row.client_secret_expires_at,
        "redirect_uris": _loads(row.redirect_uris) or [],
        "token_endpoint_auth_method": row.token_endpoint_auth_method,
        "grant_types": _loads(row.grant_types),
        "response_types": _loads(row.response_types),
        "scope": row.scope,
        "client_name": row.client_name,
        "client_uri": row.client_uri,
        "logo_uri": row.logo_uri,
        "contacts": _loads(row.contacts),
        "tos_uri": row.tos_uri,
        "policy_uri": row.policy_uri,
        "jwks_uri": row.jwks_uri,
        "jwks": _loads(row.jwks),
        "software_id": row.software_id,
        "software_version": row.software_version,
        "created_at": row.created_at,
    }


def _pending_row_to_dict(row: OAuthPendingAuthorization) -> Dict[str, Any]:
    return {
        "nonce": row.nonce,
        "client_id": row.client_id,
        "redirect_uri": row.redirect_uri,
        "redirect_uri_provided_explicitly": bool(row.redirect_uri_provided_explicitly),
        "state": row.state,
        "scopes": _loads(row.scopes),
        "code_challenge": row.code_challenge,
        "resource": row.resource,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
    }


def _code_row_to_dict(row: OAuthAuthorizationCode) -> Dict[str, Any]:
    return {
        "code": row.code,
        "client_id": row.client_id,
        "redirect_uri": row.redirect_uri,
        "redirect_uri_provided_explicitly": bool(row.redirect_uri_provided_explicitly),
        "scopes": _loads(row.scopes) or [],
        "code_challenge": row.code_challenge,
        "resource": row.resource,
        "subject": row.subject,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
    }


def _access_token_row_to_dict(row: OAuthAccessToken) -> Dict[str, Any]:
    return {
        "token": row.token,
        "client_id": row.client_id,
        "scopes": _loads(row.scopes) or [],
        "resource": row.resource,
        "subject": row.subject,
        "claims": _loads(row.claims),
        "created_at": row.created_at,
        "expires_at": row.expires_at,
    }


def _refresh_token_row_to_dict(row: OAuthRefreshToken) -> Dict[str, Any]:
    return {
        "token": row.token,
        "client_id": row.client_id,
        "scopes": _loads(row.scopes) or [],
        "subject": row.subject,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
    }


class McpOAuthStore:
    """Durable persistence for the InvestYo MCP OAuth 2.1 authorization server.

    ``db_url=None`` resolves through ``db_config.resolve_database_url()``
    (SQLite by default, Postgres when ``DATABASE_URL`` is set) -- the same
    seam every other store in this codebase uses. No ``readonly`` parameter:
    there is no read-only consumer of this store in this task's scope.
    """

    def __init__(self, db_url: Optional[str] = None) -> None:
        db_url = db_url or resolve_database_url()
        self.engine = create_db_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------

    def get_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        with session_scope(self.Session) as session:
            row = session.query(OAuthClient).filter_by(client_id=client_id).first()
            if row is None:
                return None
            return _client_row_to_dict(row)

    def register_client(self, client_info: Dict[str, Any]) -> None:
        """Upsert by ``client_id`` -- a client re-registering with the same id
        updates its stored fields rather than failing or duplicating."""
        client_id = client_info["client_id"]
        created_at = client_info.get("created_at", time.time())

        with session_scope(self.Session) as session:
            row = session.query(OAuthClient).filter_by(client_id=client_id).first()
            if row is None:
                row = OAuthClient(client_id=client_id, created_at=created_at)
                session.add(row)

            row.client_secret = client_info.get("client_secret")
            row.client_id_issued_at = client_info.get("client_id_issued_at")
            row.client_secret_expires_at = client_info.get("client_secret_expires_at")
            row.redirect_uris = _dumps(client_info.get("redirect_uris") or [])
            row.token_endpoint_auth_method = client_info.get("token_endpoint_auth_method")
            row.grant_types = _dumps(client_info.get("grant_types"))
            row.response_types = _dumps(client_info.get("response_types"))
            row.scope = client_info.get("scope")
            row.client_name = client_info.get("client_name")
            row.client_uri = client_info.get("client_uri")
            row.logo_uri = client_info.get("logo_uri")
            row.contacts = _dumps(client_info.get("contacts"))
            row.tos_uri = client_info.get("tos_uri")
            row.policy_uri = client_info.get("policy_uri")
            row.jwks_uri = client_info.get("jwks_uri")
            row.jwks = _dumps(client_info.get("jwks"))
            row.software_id = client_info.get("software_id")
            row.software_version = client_info.get("software_version")

    # ------------------------------------------------------------------
    # Pending authorizations
    # ------------------------------------------------------------------

    def save_pending_authorization(self, nonce: str, data: Dict[str, Any]) -> None:
        created_at = data.get("created_at", time.time())
        with session_scope(self.Session) as session:
            row = session.query(OAuthPendingAuthorization).filter_by(nonce=nonce).first()
            if row is None:
                row = OAuthPendingAuthorization(nonce=nonce)
                session.add(row)
            row.client_id = data["client_id"]
            row.redirect_uri = data["redirect_uri"]
            row.redirect_uri_provided_explicitly = bool(data.get("redirect_uri_provided_explicitly", False))
            row.state = data.get("state")
            row.scopes = _dumps(data.get("scopes"))
            row.code_challenge = data["code_challenge"]
            row.resource = data.get("resource")
            row.created_at = created_at
            row.expires_at = data["expires_at"]

    def load_pending_authorization(self, nonce: str) -> Optional[Dict[str, Any]]:
        with session_scope(self.Session) as session:
            row = session.query(OAuthPendingAuthorization).filter_by(nonce=nonce).first()
            if row is None:
                return None
            if row.expires_at is not None and row.expires_at < time.time():
                return None
            return _pending_row_to_dict(row)

    def delete_pending_authorization(self, nonce: str) -> None:
        with session_scope(self.Session) as session:
            session.query(OAuthPendingAuthorization).filter_by(nonce=nonce).delete()

    # ------------------------------------------------------------------
    # Authorization codes
    # ------------------------------------------------------------------

    def save_authorization_code(self, code: str, data: Dict[str, Any]) -> None:
        created_at = data.get("created_at", time.time())
        with session_scope(self.Session) as session:
            row = OAuthAuthorizationCode(
                code=code,
                client_id=data["client_id"],
                redirect_uri=data["redirect_uri"],
                redirect_uri_provided_explicitly=bool(data.get("redirect_uri_provided_explicitly", False)),
                scopes=_dumps(data.get("scopes") or []),
                code_challenge=data["code_challenge"],
                resource=data.get("resource"),
                subject=data.get("subject"),
                created_at=created_at,
                expires_at=data["expires_at"],
            )
            session.add(row)

    def load_authorization_code(self, code: str) -> Optional[Dict[str, Any]]:
        with session_scope(self.Session) as session:
            row = session.query(OAuthAuthorizationCode).filter_by(code=code).first()
            if row is None:
                return None
            if row.expires_at is not None and row.expires_at < time.time():
                return None
            return _code_row_to_dict(row)

    def delete_authorization_code(self, code: str) -> None:
        with session_scope(self.Session) as session:
            session.query(OAuthAuthorizationCode).filter_by(code=code).delete()

    # ------------------------------------------------------------------
    # Access tokens
    # ------------------------------------------------------------------

    def save_access_token(self, token: str, data: Dict[str, Any]) -> None:
        created_at = data.get("created_at", time.time())
        with session_scope(self.Session) as session:
            row = OAuthAccessToken(
                token=token,
                client_id=data["client_id"],
                scopes=_dumps(data.get("scopes") or []),
                resource=data.get("resource"),
                subject=data.get("subject"),
                claims=_dumps(data.get("claims")),
                created_at=created_at,
                expires_at=data.get("expires_at"),
            )
            session.add(row)

    def load_access_token(self, token: str) -> Optional[Dict[str, Any]]:
        with session_scope(self.Session) as session:
            row = session.query(OAuthAccessToken).filter_by(token=token).first()
            if row is None:
                return None
            if row.expires_at is not None and row.expires_at < time.time():
                return None
            return _access_token_row_to_dict(row)

    def delete_access_token(self, token: str) -> None:
        with session_scope(self.Session) as session:
            session.query(OAuthAccessToken).filter_by(token=token).delete()

    # ------------------------------------------------------------------
    # Refresh tokens
    # ------------------------------------------------------------------

    def save_refresh_token(self, token: str, data: Dict[str, Any]) -> None:
        created_at = data.get("created_at", time.time())
        with session_scope(self.Session) as session:
            row = OAuthRefreshToken(
                token=token,
                client_id=data["client_id"],
                scopes=_dumps(data.get("scopes") or []),
                subject=data.get("subject"),
                created_at=created_at,
                expires_at=data.get("expires_at"),
            )
            session.add(row)

    def load_refresh_token(self, token: str) -> Optional[Dict[str, Any]]:
        with session_scope(self.Session) as session:
            row = session.query(OAuthRefreshToken).filter_by(token=token).first()
            if row is None:
                return None
            if row.expires_at is not None and row.expires_at < time.time():
                return None
            return _refresh_token_row_to_dict(row)

    def delete_refresh_token(self, token: str) -> None:
        with session_scope(self.Session) as session:
            session.query(OAuthRefreshToken).filter_by(token=token).delete()

    # ------------------------------------------------------------------
    # Token revocation (RFC 7009) -- idempotent no-op if neither table
    # has a matching row (revoking an already-invalid/unknown token is
    # not an error).
    # ------------------------------------------------------------------

    def revoke_token(self, token: str) -> None:
        with session_scope(self.Session) as session:
            session.query(OAuthAccessToken).filter_by(token=token).delete()
            session.query(OAuthRefreshToken).filter_by(token=token).delete()

    # ------------------------------------------------------------------
    # Login lockout -- singleton row (id=1), lazily get-or-create.
    # ------------------------------------------------------------------

    def _get_or_create_login_state(self, session) -> OAuthLoginState:
        row = session.query(OAuthLoginState).filter_by(id=1).first()
        if row is None:
            row = OAuthLoginState(id=1, fail_count=0, locked_until=None)
            session.add(row)
            session.flush()
        return row

    def record_login_failure(self) -> bool:
        """Increments the failure count; sets ``locked_until`` the moment
        ``fail_count`` first reaches ``LOGIN_LOCKOUT_THRESHOLD``.

        Returns ``True`` only on the call that crosses the threshold (so a
        caller can e.g. log a distinct "account locked" event exactly once).
        """
        with session_scope(self.Session) as session:
            row = self._get_or_create_login_state(session)
            row.fail_count = (row.fail_count or 0) + 1
            if row.fail_count == LOGIN_LOCKOUT_THRESHOLD:
                row.locked_until = time.time() + LOGIN_LOCKOUT_SECONDS
                return True
            return False

    def is_locked_out(self) -> bool:
        with session_scope(self.Session) as session:
            row = self._get_or_create_login_state(session)
            return row.locked_until is not None and row.locked_until > time.time()

    def reset_login_state(self) -> None:
        with session_scope(self.Session) as session:
            row = self._get_or_create_login_state(session)
            row.fail_count = 0
            row.locked_until = None

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def purge_expired(self) -> None:
        """Deletes expired rows from every TTL'd table. Not required for
        correctness (every ``load_*`` self-checks expiry inline) -- this is
        pure housekeeping to keep the tables from growing unbounded.

        ``expires_at`` is nullable on access/refresh tokens (``None`` means
        "never expires"), so the filter excludes ``NULL`` rows there too --
        SQL's ``column < value`` is already ``NULL``-safe (a ``NULL``
        comparison never evaluates true), but this is called out explicitly
        since correctness here matters.
        """
        now = time.time()
        with session_scope(self.Session) as session:
            session.query(OAuthPendingAuthorization).filter(
                OAuthPendingAuthorization.expires_at < now
            ).delete()
            session.query(OAuthAuthorizationCode).filter(
                OAuthAuthorizationCode.expires_at < now
            ).delete()
            session.query(OAuthAccessToken).filter(
                OAuthAccessToken.expires_at.isnot(None),
                OAuthAccessToken.expires_at < now,
            ).delete()
            session.query(OAuthRefreshToken).filter(
                OAuthRefreshToken.expires_at.isnot(None),
                OAuthRefreshToken.expires_at < now,
            ).delete()
