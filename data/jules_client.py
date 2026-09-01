"""HTTP client for Google's Jules coding-agent REST API
(https://jules.googleapis.com/v1alpha/) — the single network seam every Jules
consumer in this platform goes through. See docs/JULES_INTEGRATION.md for the
full setup/safety writeup.

CORRECTED CAPABILITY MODEL (2026-08-31, operator-confirmed)
--------------------------------------------------------------
This module was originally built around a capability Jules does not actually
have. It assumed: "given a prompt and a connected GitHub repo/branch, Jules
writes NEW code and opens a real, unsupervised PR from scratch" (wired via a
hardcoded ``automationMode=AUTO_CREATE_PR`` on the ``POST /sessions`` body).

The operator has confirmed the real, corrected model: **Jules can only audit
or review an existing PR or an existing codebase. It cannot write new code or
open a PR from a prompt alone — it cannot "build from nothing."**

As a direct result, :func:`dispatch_session` — the function that assumed the
write/PR-creation capability — is now permanently disabled: it raises
:class:`JulesCapabilityNotAvailable` unconditionally, as the very first thing
it does, regardless of any argument (including ``confirm=True``) or setting.
It makes no network call and has no code path that could ever dispatch a
session again. This is not a temporary gate — the capability it assumed
simply does not exist for this integration to invoke.

:func:`list_sources` and :func:`format_sources` are unaffected. They are
read-only and capability-agnostic — they just enumerate which GitHub repos
are connected to the operator's Jules account — and remain the only working
capability in this module today.

Credential handling — ``settings.JULES_API_KEY``, NEVER ``os.environ``
------------------------------------------------------------------------
Same rule as every other credential in this codebase (see
``data/fmp_client.py``'s own docstring for the full incident history):
pydantic-settings' ``env_file=".env"`` populates the ``settings`` singleton
directly, NOT the real process ``os.environ``. The read below is therefore a
lazy ``from settings import settings`` **inside** each function, never at
module scope, so a test can monkeypatch the singleton and import of this
module never touches configuration.

Setting ``JULES_API_KEY`` alone changes nothing — ``settings.JULES_ENABLED``
must also be explicitly true (see settings.py's own field description for why
its default is False and why it is a ``settings_keysets.DANGEROUS_KEYS``
member). This still governs :func:`list_sources`, which remains a real,
working call.

Real implementation, not a scaffold (for what remains)
---------------------------------------------------------
``list_sources`` makes a real HTTP call against the Jules REST API
(``GET /sources``) — every consumer of this module (the MCP tools, the CLI
script, their tests) is written against this exact signature and body.

Error-contract discipline: ``response.json()`` is always called INSIDE the
same ``try/except`` that wraps the raw ``requests`` call — a malformed/empty
2xx body must degrade the same way a transport error or non-2xx status does,
never escape as a raw ``JSONDecodeError``. See CONSTRAINT #6 below.
"""

from __future__ import annotations

from typing import Any, Dict, List

import requests

JULES_BASE_URL = "https://jules.googleapis.com/v1alpha"


class JulesUnavailable(Exception):
    """Raised when a Jules request could not be served.

    Named for the CONDITION (Jules is not serving us this call) rather than
    for any one cause of it: a missing/rejected key, JULES_ENABLED=False, an
    unknown ``source``, or an HTTP failure all leave the caller with the same
    fact and the same remedy — do not dispatch, surface a clear message.

    CONSTRAINT #6: callers at the MCP-tool / CLI boundary catch this and
    return a clear string; it never crosses the MCP transport boundary as a
    raw exception (mirrors ``FMPUnavailable``'s own contract in
    ``data/fmp_client.py``).
    """


class JulesCapabilityNotAvailable(RuntimeError):
    """Raised whenever code attempts to have Jules write new code or open a
    pull request from a prompt alone. Jules cannot do this -- confirmed by
    the repo operator, 2026-08-31. Jules can only audit/review an existing
    PR or codebase; it has no code-writing or PR-creation capability. See
    docs/JULES_INTEGRATION.md for the full corrected capability model.
    """


def list_sources() -> Dict[str, Any]:
    """``GET /sources`` — list the GitHub repos connected to this Jules
    account. Raises :class:`JulesUnavailable` if ``JULES_API_KEY`` is unset,
    ``JULES_ENABLED`` is False, or the request fails.

    Returns the raw parsed JSON response (``{"sources": [...]}"``) — no
    reshaping, matching ``data/fmp_client.py``'s "wrappers return raw JSON,
    consumers do the mapping" convention.
    """
    from settings import settings

    if not settings.JULES_ENABLED:
        raise JulesUnavailable(
            "Jules integration is disabled (settings.JULES_ENABLED=False)."
        )
    if not settings.JULES_API_KEY:
        raise JulesUnavailable(
            "JULES_API_KEY is not set (settings.JULES_API_KEY); request skipped."
        )

    url = f"{JULES_BASE_URL}/sources"
    try:
        response = requests.get(
            url,
            headers={
                "X-Goog-Api-Key": settings.JULES_API_KEY,
                "Accept": "application/json",
            },
            timeout=settings.JULES_REQUEST_TIMEOUT_SECONDS,
        )

        status = getattr(response, "status_code", None)
        if status is None or not (200 <= int(status) < 300):
            raise JulesUnavailable(f"Jules returned HTTP {status} for GET /sources.")

        return response.json()
    except requests.RequestException as exc:
        raise JulesUnavailable(f"Jules transport error on GET /sources: {exc}") from exc
    except ValueError as exc:
        # response.json() raises a json.JSONDecodeError (a ValueError
        # subclass, same for stdlib json and simplejson) on a malformed or
        # empty 2xx body. This must degrade the same way a transport error
        # or non-2xx status does (CONSTRAINT #6) rather than escape as a raw
        # JSONDecodeError.
        raise JulesUnavailable(
            f"Jules returned a malformed JSON response for GET /sources: {exc}"
        ) from exc


def format_sources(sources_response: Dict[str, Any]) -> List[Dict[str, str]]:
    """Normalize a raw ``GET /sources`` response body into a flat list of
    ``{"name": str, "owner": str, "repo": str}`` dicts.

    Single shared source-list formatter for every consumer that renders
    ``list_sources()``'s output — previously ``investyo_mcp_server.py``'s
    ``list_jules_sources`` and ``scripts/jules_dispatch.py``'s
    ``_cmd_list_sources`` each reimplemented this parsing independently and
    had already drifted (different fallback strings for an unnamed source).
    ``"unknown"`` is the canonical fallback here (the MCP tool's prior
    choice; the CLI script's prior ``"<unknown>"`` is retired in favor of
    this shared one).

    Tolerates the same edge cases this module's callers must tolerate: an
    explicit ``{"sources": null}`` (``.get(...) or []``, not
    ``.get(..., [])`` — the default only applies when the key is absent) and
    a non-dict entry in the list.
    """
    sources = (
        (sources_response.get("sources") or []) if isinstance(sources_response, dict) else []
    )
    normalized: List[Dict[str, str]] = []
    for src in sources:
        if isinstance(src, dict):
            name = src.get("name") or "unknown"
            github_repo = src.get("githubRepo")
            github_repo = github_repo if isinstance(github_repo, dict) else {}
            owner = str(github_repo.get("owner", "?"))
            repo = str(github_repo.get("repo", "?"))
        else:
            name = str(src) if src is not None else "unknown"
            owner = "?"
            repo = "?"
        normalized.append({"name": name, "owner": owner, "repo": repo})
    return normalized


def dispatch_session(
    prompt: str,
    source: str,
    branch: str,
    title: str,
    *,
    force: bool = False,
    confirm: bool = False,
) -> Dict[str, Any]:
    """PERMANENTLY DISABLED. This function used to assume Jules could write
    new code and open a pull request from a prompt alone -- it cannot. See
    the module docstring's "CORRECTED CAPABILITY MODEL" section.

    Always raises :class:`JulesCapabilityNotAvailable`, unconditionally,
    regardless of any argument (including ``confirm=True``/``force=True``)
    or any setting. Makes no network call and has no remaining code path
    that could ever dispatch a session.

    The signature is kept unchanged so existing call sites do not break at
    the call-site syntax level -- only the behavior changed to an immediate,
    unconditional raise.
    """
    raise JulesCapabilityNotAvailable(
        "dispatch_session() assumed Jules could write new code and open a PR "
        "from a prompt alone (hardcoded automationMode=AUTO_CREATE_PR). This "
        "capability does not exist -- Jules can only audit/review an existing "
        "PR or codebase. This dispatch path is permanently disabled. See "
        "docs/JULES_INTEGRATION.md for the corrected capability model."
    )
