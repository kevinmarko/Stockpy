"""Committed key-set constants for the runtime settings store.

This module answers two narrow questions about ``settings.Settings`` fields:

``BOOTSTRAP_KEYS``
    Which fields must NEVER be sourced from the runtime settings store — not
    "needs a restart to take effect", but "even *storing* the override is
    wrong".

``DANGEROUS_KEYS``
    Which fields, when written through *any* settings editor, should require an
    explicit operator confirmation step rather than applying like an ordinary
    tunable.

The two are ORTHOGONAL classifications. A field can be bootstrap-only without
being dangerous, dangerous without being bootstrap-only, or (today) neither.
They are currently disjoint — see ``tests/test_settings_keysets.py``, which
asserts that and every other structural property claimed here.

--------------------------------------------------------------------------
Why this module is a dependency-free leaf
--------------------------------------------------------------------------
A later change will add ``runtime_flags.py`` — a stdlib-only leaf at the repo
root, imported BY ``settings.py``, that loads the runtime store from a JSON
file. Because ``settings.py`` imports it, it can never import ``settings``
back. ``runtime_flags.py`` will import ``BOOTSTRAP_KEYS`` from here, so THIS
module inherits the same constraint:

    **Never import ``settings``, ``gui.env_io``, or anything non-stdlib at
    module scope here.** The names below are deliberately plain string
    literals, not derived from ``Settings.model_fields``. The test module does
    the cross-checking; this module stays importable from anywhere, including
    from inside ``settings.py``'s own import.

That is also why every claim below is stated as a comment plus a machine-
readable reason string, rather than computed: a computed set would need the
very imports this file cannot have.

--------------------------------------------------------------------------
Relationship to the OTHER key sets already in this repo
--------------------------------------------------------------------------
These constants ADD to, and do not replace or override, the existing
classifications. Nothing here changes any current runtime behavior.

``gui/env_io.py::SECRET_KEYS``
    Credentials. Masked on read, ``SecretWriteError`` on write, never
    GUI-writable at all. A strictly stricter mechanism than anything here.

``gui/env_io.py::ALLOWED_KEYS``
    The ``.env``-writer allowlist. Membership means "a GUI/API editor may
    persist this to ``.env``", which is a DIFFERENT mechanism from the runtime
    store: a ``.env`` write is durable and takes effect at next launch, and is
    unaffected by ``BOOTSTRAP_KEYS``.

``gui/env_io.py::EXCLUDED_FROM_GUI``
    The third bucket: 8 filesystem paths. Used to also hold 12 fail-closed
    write/execution gates, hand-set in ``.env`` only; those were reclassified
    into ``ALLOWED_KEYS`` on 2026-08-08 by explicit operator decision (see
    that module's own comment) and now carry no marker here at all — see
    ``HAND_SET_ONLY_KEYS`` below for where they live now.

``api/pilots_api.py``'s five scoped editors
    ``_TUNABLE_INDEX`` (46) / ``_SENTIMENT_INDEX`` (33) /
    ``_SECTOR_SELECTION_INDEX`` (11) / ``_FMP_INDEX`` (24) /
    ``_ETF_TRANSMISSION_INDEX`` (19) = 133 keys, each already shipped with its
    own ``GET``/``PUT`` pair and write-permission gate.

``docs/settings_liveness.json``
    Per-field ``live_safe`` / ``restart_required`` / ``no_op``. Necessary
    context for ``BOOTSTRAP_KEYS`` but NOT sufficient: ``restart_required`` is
    an ordinary, expected outcome for ~78 fields (captured in some
    constructor, read once at module scope, …) and the hot-reload UI is meant
    to surface that honestly. ``BOOTSTRAP_KEYS`` is the much smaller set where
    the store should refuse to hold a value at all.
"""

from __future__ import annotations


# ==========================================================================
# BOOTSTRAP_KEYS
# ==========================================================================
# Structural exclusions from the runtime settings store.
#
# The bar each entry has to clear: a stored override would leave the process
# in a state that is WORSE than not having applied the change — self-
# inconsistent, self-referential, or split across two incompatible resources —
# rather than merely "the old value is still in force until you restart".
#
# Deliberately EXCLUDED, with evidence, so a later pass does not re-add them:
#
#   * ``LOG_LEVEL`` — "logging configuration read before anything else
#     initializes" is a real bootstrap category in general, but it has ZERO
#     qualifying fields in THIS tree. Every module-scope
#     ``logging.basicConfig()`` call in production code hardcodes
#     ``logging.INFO``; the only one that consumes ``settings.LOG_LEVEL`` is
#     ``gui/app.py:83``, and ``gui/`` is decommissioned (AGENTS.md §2). The one
#     other reader, ``alerting.py:118``, goes through
#     ``os.environ.get("LOG_LEVEL", …)``, which no mutation of the settings
#     singleton can ever reach — that makes it unreachable-by-the-store, not
#     unsafe-to-store. Storing ``LOG_LEVEL`` is harmless; applying it to an
#     already-configured root logger needs a restart, which is the ordinary
#     ``restart_required`` outcome. Corroborating evidence: it is already
#     exposed by the shipped ``_TUNABLE_INDEX`` editor, and that editor is not
#     broken.
#
#   * The other 6 ``EXCLUDED_FROM_GUI`` filesystem paths
#     (``PROMPT_CACHE_DIR``, ``WATCH_RULES_FILE``, ``ALERT_FILE_PATH``,
#     ``GRAVITY_AI_RUNNER_OUTPUT_PATH``, ``LLM_COMMENTARY_CACHE_PATH``,
#     ``SYNC_WATCHLIST_FILES``) — these are consumer paths. None of them can
#     participate in resolving the store's OWN location, so none is
#     self-referential the way ``OUTPUT_DIR`` is.
#
# Every entry below is ``restart_required`` in ``docs/settings_liveness.json``
# EXCEPT the two ports, which that classifier reports ``live_safe``. That
# disagreement is real, understood, and asserted explicitly in the test module
# rather than suppressed — see ``ORCHESTRATOR_API_PORT``'s reason below and
# ``tests/test_settings_keysets.py::TestLivenessCrossReference``.

BOOTSTRAP_KEY_REASONS: dict[str, str] = {
    # ---- self-referential: the store's own file location -------------------
    "OUTPUT_DIR": (
        "Self-referential. Every JSON state file this platform writes lives "
        "under OUTPUT_DIR (state_snapshot.json, daemon.json, "
        "execution_queue.json, scan_candidates.json), so the runtime store's "
        "own file path resolves through it. A store that could relocate "
        "itself would decide where to look for the value that decides where "
        "to look. Also captured at module scope in six separate consumers "
        "(execution/kill_switch.py:43 among them), so a live change is "
        "half-applied on top of being circular."
    ),

    # ---- database connection: split-brain, not staleness -------------------
    "DATABASE_URL": (
        "A live change is data-corruption-shaped, not restart-shaped. "
        "db_config.create_db_engine() reads it once per engine construction, "
        "and every store (transactions_store, historical_store, "
        "run_history_store, cap_audit_store, sector_correlation_store, "
        "iv_engine) builds its engine in its own __init__ at a different "
        "moment. Overriding it mid-process means stores constructed before "
        "the change keep writing to database A while stores constructed "
        "after write to database B, in one process, with no error. SECRET "
        "too (may embed credentials), but that is a separate mechanism."
    ),
    "MCP_DATABASE_URL_RO": (
        "Same split-brain as DATABASE_URL, on the read-only seam. It also "
        "selects WHICH read-only enforcement applies: a restricted Postgres "
        "ROLE (a hard boundary) when set, versus the defeasible "
        "postgresql_readonly=True session GUC when unset (db_config.py's "
        "module docstring). Toggling that distinction live means two "
        "concurrently-live read-only engines in one process disagree about "
        "whether their read-only guarantee is enforceable."
    ),
    "DB_POOL_SIZE": (
        "Consumed at the same one-shot site as DATABASE_URL "
        "(db_config.create_db_engine / create_readonly_db_engine, Postgres "
        "branch only). Honest scoping: its failure mode is milder than the "
        "URL's — inconsistent pool sizing across engines, not split-brain "
        "data — but it is read by the same builder at the same moment, so "
        "splitting it from the URL would let an operator half-reconfigure "
        "one connection pool. Grouped for coherence, not equal severity."
    ),
    "DB_MAX_OVERFLOW": (
        "Identical reasoning to DB_POOL_SIZE — the second of the two "
        "Postgres pool-sizing arguments read by db_config's engine builders."
    ),

    # ---- ports: bound once, but read live by clients -----------------------
    "ORCHESTRATOR_API_PORT": (
        "Bound once, read live — the worst combination. "
        "desktop/orchestrator_daemon.py passes it to uvicorn.Config at "
        "daemon startup (lines 254, 340, 421) and the socket stays on that "
        "port for the life of the process; it is also stamped into "
        "output/daemon.json at startup. But gui/daemon_client._base_url() "
        "re-reads it on EVERY call (its docstring says so explicitly), and "
        "api/pilots_api.py imports that client for get_status / trigger_run "
        "/ set_interval, plus reports the port in its own status payload "
        "(line 2887). A stored override therefore does not go stale — it "
        "actively splits the process: the server listening on the old port, "
        "the client dialling the new one, and the status surface reporting a "
        "port nothing is bound to. Note docs/settings_liveness.json says "
        "live_safe; that is a correct answer to a narrower question (the "
        "read is fresh) and is exactly why the socket bind has to be "
        "reasoned about here instead."
    ),
    "PILOTS_API_PORT": (
        "Same one-shot uvicorn.Config bind as ORCHESTRATOR_API_PORT "
        "(desktop/orchestrator_daemon.py:280) and likewise echoed into "
        "output/daemon.json as pilots_api_port at startup. Included as the "
        "matching half of the pair so the two ports cannot drift apart in "
        "how they are treated. Also classified live_safe by the liveness "
        "classifier, for the same narrower-question reason."
    ),
}

BOOTSTRAP_KEYS: frozenset[str] = frozenset(BOOTSTRAP_KEY_REASONS)


# ==========================================================================
# DANGEROUS_KEYS
# ==========================================================================
# Fields whose write should require explicit operator confirmation ("type the
# field name to confirm"), even when the write is well-formed and applies
# successfully. This is a SAFETY classification about the consequence of the
# new value, not about whether the change can take effect.
#
# Evaluated against ANY field, not only fields some editor currently exposes —
# a future editor may add one, and the classification should already be there
# when it does.
#
# Two sources, kept as separate named constants so drift in either is
# attributable.

# -- Source (a): the platform's own "never GUI-writable" markers -------------
#
# Fields carrying an explicit "Never GUI-writable" / "hand-set in .env only"
# marker in settings.py, cross-checked by scripts/measure_settings_census.py
# against whether that claim currently holds.
#
# EMPTY as of 2026-08-08. Until then this held the 11 fail-closed write/
# execution gates (12 counting CACHE_LONG_SHORT_WRITES_ENABLED, added and
# already contradicting this set in the introducing PR — see the drift note
# below) that were EXCLUDED_FROM_GUI. Per explicit operator decision (PR #630
# audit) all 12 were reclassified into gui/env_io.py's ALLOWED_KEYS: "not
# secret information" is now the sole bar for GUI-writability, replacing the
# older "a GUI bug must never flip this on" theory this set encoded. Rather
# than simply deleting the safety signal those markers carried, all 12 were
# moved into SAFETY_CRITICAL_KEY_REASONS below instead of being dropped
# outright — they remain DANGEROUS_KEYS members (typed confirmation required
# on write, on any editor that exposes them) even though they are no longer
# hand-set-only. This set is intentionally left in place, empty, rather than
# deleted: a future genuinely-hand-set-only marker in settings.py should still
# land here, and the drift test below still enforces that.
#
# NOT included, and deliberately not second-guessed: BROKERAGE_CONNECT_ENABLED,
# UNIVERSE_SYNC_ENABLED and AGENTIC_DISCOVERY_ENABLED were moved OUT of this
# hand-set-only category and INTO ALLOWED_KEYS on 2026-08-02 by explicit
# operator decision (CLAUDE.md's env-write-safety bullet), each still
# independently gated by its own endpoint's command token — the same pattern
# the 2026-08-08 decision above generalized to the remaining 12.
#
# DRIFT: tests/test_settings_keysets.py reads
# docs/settings_field_census.json's hand_set_markers.marked_fields and asserts
# this set equals the non-secret, comment_claim_holds=True subset EXACTLY. Add
# a new hand-set-only field to settings.py without adding it here and that
# test fails loudly.
HAND_SET_ONLY_KEYS: frozenset[str] = frozenset()

# -- Source (b): safety-critical fields, marker or no marker ----------------
#
# These gate live trading behavior or API trust boundaries directly. None of
# them carries a hand-set-only marker (several are ordinary ALLOWED_KEYS
# tunables), which is precisely why they need naming here: the marker sweep
# alone would miss every one.
SAFETY_CRITICAL_KEY_REASONS: dict[str, str] = {
    "ADVISORY_ONLY": (
        "The execution quarantine. Default True; when True, ALL broker order "
        "submission is suppressed. AGENTS.md §2 calls this load-bearing "
        "safety infrastructure, not a feature flag to casually flip. A "
        "silent False here is the single highest-consequence write in the "
        "whole settings surface."
    ),
    "DRY_RUN": (
        "The second execution quarantine — OrderManager._submit_with_retry "
        "checks intent.dry_run before touching any broker, and that "
        "manager-level check is the authoritative one (CLAUDE.md). Turning "
        "it off is what makes logged orders become submitted orders."
    ),
    "ROBINHOOD_EXECUTION_MODE": (
        "off | review | live. Moving this to 'live' is what lets the "
        "Robinhood execution bridge place real orders; AGENTS.md §2 lists it "
        "alongside ADVISORY_ONLY and the kill switch as one of the "
        "independent execution gates that must not be weakened."
    ),
    "MACRO_REGIME_GATE_ENABLED": (
        "The recession/credit-event BUY veto. When True, "
        "MacroEconomicDTO.killSwitch (Sahm Rule >= 0.5, VIX > 30, or HY OAS "
        "> 6%) vetoes new BUY orders. Setting it False bypasses that veto "
        "entirely — and scripts/preflight_check.py treats gate-off as a "
        "BLOCKING pre-live failure when ALPACA_PAPER=False, which is the "
        "repo's own statement that this is not a routine toggle."
    ),
    "BROKER_BACKEND": (
        "Selects which broker actually receives orders: 'alpaca' (real "
        "broker) vs. 'fmp_paper' (a local SQLite paper ledger via "
        "execution/fmp_paper_broker.py -- no order ever reaches a real "
        "market). execution/broker_selection.py::resolve_broker_backend() "
        "now force-falls-back to 'alpaca' with a CRITICAL alert when this "
        "is 'fmp_paper' AND the run is genuinely going live "
        "(ADVISORY_ONLY=False and ALPACA_PAPER=False), and both "
        "main_orchestrator.py and robinhood_execution_mcp.py route through "
        "that one shared guard. But a runtime guard on the CONSTRUCTED "
        "broker is not the same protection as gating the SETTING itself --"
        " a silent flip via a settings-editor write is still the single "
        "field that decides whether an order is real or a paper no-op, "
        "matching the same justification MACRO_REGIME_GATE_ENABLED already "
        "gets in this same dict."
    ),
    "FMP_BARS_ENABLED": (
        "Its own field description says to read FMP_BARS_ADJUSTMENT before "
        "enabling, because an adjustment-convention mismatch corrupts every "
        "return series, indicator, GARCH fit and backtest — PLAUSIBLY, with "
        "nothing failing loudly. CLAUDE.md makes scripts/verify_fmp_bars.py "
        "a hard gate before this is ever flipped on live."
    ),
    "FMP_BARS_ADJUSTMENT": (
        "The single highest-risk value in the FMP integration. 'light' and "
        "'full' are SPLIT-ONLY while the incumbent yfinance path is split "
        "AND dividend adjusted, so 'full' is the obvious-looking pick and it "
        "is wrong. Worse, price_bars has a (symbol, date) PK, so changing "
        "this against an existing DB SPLICES two adjustment conventions into "
        "one series at the cutover date — which no test catches."
    ),
    "CORS_ALLOWED_ORIGINS": (
        "Security-relevant without being a credential: it decides which "
        "browser origins the State API and Pilots API accept requests from. "
        "Read at module scope by five separate API modules "
        "(control_api / data_api / metrics_api / pilots_api / state_api), so "
        "a change is also only half-observable in a running process."
    ),
    # -- 2026-08-08: moved here from HAND_SET_ONLY_KEYS (PR #630 audit) ------
    # These 12 are the fail-closed write/execution master switches that used
    # to be gui/env_io.py's EXCLUDED_FROM_GUI + this module's source (a). Per
    # explicit operator decision, "not secret" is now sufficient for
    # GUI-writability on its own, so none of them is hand-set-only anymore —
    # but each still gates a real side effect a silent flip would be bad for,
    # so they land here instead of being dropped from DANGEROUS_KEYS
    # entirely: still confirmation-required on write, now via ALLOWED_KEYS
    # rather than via being unreachable. Reasons kept short since the full
    # rationale for each already lives in its own settings.py Field comment
    # and api/pilots_api.py `require_*_enabled` docstring.
    "AI_GENERATION_API_ENABLED": (
        "Master gate for api/data_api.py's three paid Claude/Gemini/Opal "
        "generation endpoints, which are otherwise reachable over a "
        "fail-open HTTP API. A silent flip turns on real, billed external "
        "API calls triggerable by anyone who can reach the Data API."
    ),
    "AUTOMATION_WRITES_ENABLED": (
        "Gates POST /automation/resume, which re-enables live order "
        "submission after ADVISORY_ONLY was previously engaged — a silent "
        "flip re-arms order flow the operator may have paused deliberately."
    ),
    "BROKERAGE_REFRESH_ENABLED": (
        "Gates POST /brokerage/refresh, a real live login against the "
        "operator's actual brokerage account bypassing the daily cache — "
        "not a simulated or sandboxed action."
    ),
    "CACHE_LONG_SHORT_WRITES_ENABLED": (
        "Gates POST /pilots/cache-long-short/{start,approve-bulk}, which "
        "persists a new tracked position or approves a TLH recommendation — "
        "changes what a trading strategy recommends."
    ),
    "COMMAND_EXECUTION_ENABLED": (
        "The highest-risk flag in this group: enables the 'command' job "
        "type on POST /jobs, which can execute the global kill switch, a "
        "forced Robinhood re-login, or arbitrary flags to the "
        "orchestrators via a manifest-listed CLI target."
    ),
    "DEAD_LETTER_RETRY_ENABLED": (
        "Gates POST /dead-letter/retry, which spawns a real main.py "
        "subprocess (network calls, a fresh data fetch, a real advisory "
        "evaluation) for one symbol."
    ),
    "GENERAL_SETTINGS_WRITES_ENABLED": (
        "Gates PUT /settings/tunables — Kelly sizing, risk-gate, and "
        "forecasting knobs that change how large a position gets and when "
        "the risk gate blocks an order."
    ),
    "LLM_WRITES_ENABLED": (
        "Gates PUT /llm/setting — which LLM provider narrates a rationale "
        "and whether the Gravity AI runner / Opal research agent can fire."
    ),
    "MACRO_GATE_WRITES_ENABLED": (
        "Gates PUT /observability/macro-gate, the write path for "
        "MACRO_REGIME_GATE_ENABLED itself — the recession/credit-event "
        "BUY-veto bypass. A silent flip here silently removes that veto."
    ),
    "MCP_OAUTH_ENABLED": (
        "Decides whether investyo_mcp_server.py's OAuth authorization-server "
        "endpoints (/register, /authorize, /token, /revoke) are live. RFC "
        "7591 dynamic client registration is unauthenticated by design, so "
        "the flag alone does not gate access — MCP_OAUTH_PASSWORD, checked "
        "at the /login form, is the real trust boundary once a client can "
        "register itself and start an auth flow."
    ),
    "MCP_OAUTH_MULTI_USER_ENABLED": (
        "Switches the OAuth /login form from the single-passphrase check "
        "(MCP_OAUTH_PASSWORD) to per-user credentials in oauth_users "
        "(mcp_oauth_store.py). Flipping it changes WHICH AUTHENTICATION "
        "MECHANISM governs the whole OAuth server — the same risk class "
        "MCP_OAUTH_ENABLED itself already carries."
    ),
    "PROMPT_REGISTRY_WRITES_ENABLED": (
        "Gates PUT /prompts/pin, which changes WHICH PROMPT TEXT THE "
        "PLATFORM ACTUALLY RUNS — a real behavioral change, not a config "
        "tunable."
    ),
    "RAG_QUERY_API_ENABLED": (
        "Gates POST /rag/query, a paid external LLM call otherwise "
        "reachable behind the command token alone — same risk class as "
        "AI_GENERATION_API_ENABLED."
    ),
    "STRATEGY_WRITES_ENABLED": (
        "Gates PUT /strategy/modules — signal weights and the "
        "disabled-module set, which changes WHAT THE PLATFORM RECOMMENDS."
    ),
    "FORECAST_BACKFILL_ENABLED": (
        "Gates POST /pilots/forecast_backfill/run and "
        "POST /pilots/forecast_backfill/cancel/{job_id}, which retrain and "
        "overwrite production ml/models/meta_*.pkl files read by live "
        "inference — a real change to what the platform's scoring is "
        "trained on, not a config toggle."
    ),
    "JULES_ENABLED": (
        "Enables the Jules third-party autonomous coding-agent integration "
        "(data/jules_client.py) -- when on, dispatch_jules_task can make Jules "
        "write code and open a real, unsupervised PR on the operator's "
        "actual GitHub repo. Unlike the other flags in this group there is "
        "no internal Stockpy command-token boundary protecting this action; "
        "the per-call confirm=True argument is the only remaining gate."
    ),
}

SAFETY_CRITICAL_KEYS: frozenset[str] = frozenset(SAFETY_CRITICAL_KEY_REASONS)

# NOT included, deliberately: the 8 marker-carrying fields that are also in
# SECRET_KEYS (FMP_API_KEY, FOLLOW_API_TOKEN, ORCHESTRATOR_DAEMON_TOKEN,
# PROMPT_REGISTRY_PUBLISH_TOKEN, PROMPT_REGISTRY_SIGNING_KEY,
# PROMPT_REGISTRY_TOKEN, PROMPT_REGISTRY_URL, STATE_API_TOKEN). Secrets are
# already handled by a strictly stricter, completely separate mechanism —
# masked on read, SecretWriteError on write, never editable at all. Tagging
# them "needs confirmation" would be redundant rather than wrong, and worse,
# it would blur two categories that should stay distinct: DANGEROUS_KEYS means
# "you may do this, but prove you meant it", while SECRET_KEYS means "you may
# not do this here at all". A confirmation prompt on a field the write layer
# is going to refuse anyway is a UI that teaches operators to expect a
# confirmable path to a secret. Asserted disjoint in the test module.

DANGEROUS_KEYS: frozenset[str] = HAND_SET_ONLY_KEYS | SAFETY_CRITICAL_KEYS
