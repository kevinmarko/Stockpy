"""Feature flags registry for the Pilots API's Feature Flags settings screen
(``GET``/``PUT``/``PATCH /settings/feature-flags`` in ``api/pilots_api.py``).

Surfaces three tiers of flag into ONE list so an operator has a single,
clearly-labeled place to see and toggle every admin/write/execution gate and
read-only diagnostic feature, instead of discovering a fail-closed flag only
when an endpoint 403s:

``settings_keysets.DANGEROUS_KEYS`` (imported, never copied)
    The existing, already-enforced "requires typed confirmation on write"
    set (20 members as of 2026-08-08's PR #630 audit -- deliberately not
    hardcoded here, since importing rather than copying means any future
    addition to ``settings_keysets.SAFETY_CRITICAL_KEY_REASONS`` is
    automatically surfaced here with zero new code, which a hardcoded count
    in this docstring would only contradict the next time that happens).

``WRITE_GATE_REASONS``
    Real ``require_*_enabled``-style write/execution gates that are NOT in
    ``DANGEROUS_KEYS`` -- several by explicit, documented prior decision
    (see ``settings_keysets.py``'s own comment: "NOT included, and
    deliberately not second-guessed: BROKERAGE_CONNECT_ENABLED,
    UNIVERSE_SYNC_ENABLED and AGENTIC_DISCOVERY_ENABLED..."). These still
    belong in the Feature Flags screen for visibility -- an operator should
    be able to find and flip them -- but do NOT require typed confirmation,
    matching that existing decision. Do not add a key here that is already
    in ``settings_keysets.DANGEROUS_KEYS``; if a flag should require typed
    confirmation, it belongs in ``SAFETY_CRITICAL_KEY_REASONS`` instead, not
    here (see ``tests/test_feature_flags_registry.py``'s disjointness check).

``DIAGNOSTIC_FLAG_REASONS``
    Read-only measurement/data-source master switches that feed no scoring
    or sizing decision (curated 2026-08-07 by explicit operator decision) --
    visible for discoverability, not writes-are-risky.
"""

import settings_keysets

# -- Real write/execution gates NOT in settings_keysets.DANGEROUS_KEYS ------
#
# Each entry names the actual endpoint(s) the flag gates, verified against
# the guard function/site in api/*.py -- not guessed from the flag's name.
WRITE_GATE_REASONS: dict[str, str] = {
    "BROKERAGE_CONNECT_ENABLED": (
        "Gates POST /brokerage/connect and /brokerage/disconnect on the "
        "Pilots API (api/pilots_api.py::require_brokerage_connect_enabled) "
        "-- real brokerage-credential intake, independently gated by "
        "FOLLOW_API_TOKEN and a loopback-only (127.0.0.1) request check."
    ),
    "UNIVERSE_SYNC_ENABLED": (
        "Gates POST /data/sync on the Data API "
        "(api/data_api.py::require_ai_capability_enabled('UNIVERSE_SYNC_ENABLED', ...)) "
        "-- refreshes the tracked ticker universe from the configured "
        "sources, on top of the existing STATE_API_TOKEN write-token guard."
    ),
    "AGENTIC_DISCOVERY_ENABLED": (
        "Gates PUT /agentic/scan-config on the Pilots API "
        "(api/pilots_api.py::require_agentic_discovery_enabled) -- writes "
        "the Robinhood broker-scan configuration the agentic-discovery "
        "skill consumes, on top of FOLLOW_API_TOKEN."
    ),
    "JOBS_API_ENABLED": (
        "Gates background job execution and SSE log-streaming endpoints on "
        "the orchestrator Control API (api/control_api.py) -- an inline "
        "check, not a require_*_enabled dependency, since this flag guards "
        "a whole route group rather than one write endpoint."
    ),
    "PILOTS_API_ENABLED": (
        "Controls whether the Pilots API is hosted inside the persistent "
        "orchestrator daemon process at all (desktop/orchestrator_daemon.py) "
        "-- a process-startup switch consulted once at daemon boot, not a "
        "per-request guard. Flipping this off removes the entire Pilots "
        "API surface, including this screen, until the daemon restarts."
    ),
    "RLHF_CALIBRATION_ENABLED": (
        "Gates the RLHF Calibration Review Queue's write endpoints "
        "(POST /rlhf/proposals, /rlhf/proposals/{id}/review, "
        "/rlhf/export-sft) on the Pilots API. Defaults True: every "
        "proposal here is hypothetical and paper-only, no capital or "
        "broker involvement (rlhf_calibration_store.py)."
    ),
    "PAPER_BROKER_WRITES_ENABLED": (
        "Gates POST /pilots/paper-broker/reset on the Pilots API "
        "(api/pilots_api.py::require_paper_broker_writes_enabled) -- wipes "
        "the local FMP paper account's positions/orders and reseeds cash. "
        "Defaults True: no real money or broker is involved "
        "(data/paper_account_store.py), unlike BROKER_BACKEND itself (in "
        "settings_keysets.DANGEROUS_KEYS) which decides whether an order is "
        "real."
    ),
    "FIX_GATEWAY_ENABLED": (
        "Gates POST /pilots/execution/fix/route and the FIX session-"
        "management endpoints (test-request, reset-seq, reconnect, "
        "session/status) on the Pilots API "
        "(api/pilots_api.py::require_fix_gateway_enabled), on top of their "
        "existing command/read-token checks. Defaults True: the FIX 4.4 "
        "gateway (execution/fix_gateway.py) is fully simulated -- it never "
        "opens a real venue connection or touches real capital."
    ),
}

# -- Read-only diagnostic/data features (2026-08-07, opt-in by operator -----
# decision) -- feed no scoring or sizing decision, visible for
# discoverability rather than write risk.
DIAGNOSTIC_FLAG_REASONS: dict[str, str] = {
    "SECTOR_HEAT_ENABLED": "Enables Sector Heat Factor computation from GDELT article volume.",
    "WIKIPEDIA_ATTENTION_ENABLED": "Enables Attention Score computation from Wikipedia pageviews.",
    "ETF_HOLDINGS_ENABLED": "Enables fetching ETF constituent baskets for exposure analysis.",
    "ETF_TRANSMISSION_ENABLED": "Enables ETF volatility-transmission measurement columns (diagnostic only -- not read by scoring or sizing).",
    "MARKET_DATA_LATENCY_TRACKING_ENABLED": "Tracks and surfaces real-time market data feed latency.",
    "SENTIMENT_INDEX_ENABLED": "Computes composite sentiment index from news and reviews.",
    "EDGAR_FULLTEXT_ENABLED": "Enables full-text ingestion of 10-K/10-Q SEC filings.",
}

# The unified set of all feature-flag keys exposed in the Feature Flags
# screen. Inherits settings_keysets.DANGEROUS_KEYS so any future addition
# there is automatically exposed here with no further code change.
FEATURE_FLAG_KEYS: frozenset[str] = frozenset(
    settings_keysets.DANGEROUS_KEYS
    | set(WRITE_GATE_REASONS)
    | set(DIAGNOSTIC_FLAG_REASONS)
)
