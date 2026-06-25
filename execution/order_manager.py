"""
execution/order_manager.py
===========================
Stateful order submission layer sitting between the signal/sizing pipeline and
the broker adapter.

Responsibilities
----------------
1. **Kill-switch gate** — if the global kill switch is active,
   ``submit_order_with_idempotency`` raises ``KillSwitchActiveError`` before
   any pre-trade check or broker call.

2. **Pre-trade risk gate** — every OrderIntent must pass
   ``PreTradeRiskGate.run_all()`` before reaching the broker.  An injected
   ``RiskContext`` supplies per-call live data (account snapshot, open
   positions, macro state, historical returns).

3. **Idempotency** — every ``OrderIntent`` is assigned a deterministic
   ``client_order_id`` derived from (strategy_id, timestamp_bucket, symbol,
   side, qty).  Submitting the same intent twice within the same bucket
   produces the same ID; the broker deduplicates it, and the local
   ``_submitted`` set prevents even a second network call.

4. **Transient-error retry** — single linear back-off retry (configurable)
   for transient errors before giving up.

5. **State reconciliation** — ``reconcile_state`` compares internal
   ``TransactionsStore`` active positions with the broker's ground truth every
   cycle, logging CRITICAL on any drift and optionally posting a webhook alert.

6. **Dry-run mode** — when ``dry_run=True``, intents are logged but never
   forwarded to the broker; returns synthetic OrderResult with status=ACCEPTED.

Kill-switch integration
-----------------------
``GlobalKillSwitch`` is injected at construction.  If ``None`` (default), a
fresh instance is constructed pointing at ``settings.OUTPUT_DIR/KILL_SWITCH``.
Pass an instance with a temp-dir path in tests that need to control the file.

Risk-gate integration
---------------------
``PreTradeRiskGate`` is injected at construction.  If ``None``, the gate is
**skipped entirely** (safe default for backward-compat and tests that only
care about idempotency / reconciliation).  Pass an instance to enable.

Alerting
--------
Set ``ALERT_WEBHOOK_URL`` in ``.env`` to a Slack / Discord incoming-webhook
URL.  On reconciliation drift, an HTTP POST is sent.  Failures are logged but
never crash the reconciliation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from execution.broker_base import (
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderStatus,
)
from execution.kill_switch import GlobalKillSwitch, KillSwitchActiveError
from execution.risk_gate import PreTradeRiskGate, RiskContext
from settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Deterministic order-id generation
# ---------------------------------------------------------------------------

_BUCKET_SECONDS = 60  # same intent submitted within 60 s → same client_order_id


def make_client_order_id(
    strategy_id: str,
    symbol: str,
    side: str,
    qty: float,
    *,
    timestamp: Optional[datetime] = None,
    bucket_seconds: int = _BUCKET_SECONDS,
) -> str:
    """Return a deterministic, URL-safe client_order_id (≤ 48 chars).

    The ID is a 48-hex-char SHA-256 prefix over a canonical string built from
    the provided fields.  ``timestamp`` is bucketed to ``bucket_seconds``
    so the same intent re-submitted within the window yields the same ID.

    Alpaca's client_order_id max length is 128 chars; 48 is safe.
    """
    ts = timestamp or datetime.now(timezone.utc)
    bucket = int(ts.timestamp()) // bucket_seconds
    canonical = f"{strategy_id}|{symbol.upper()}|{side.lower()}|{qty:.6f}|{bucket}"
    return hashlib.sha256(canonical.encode()).hexdigest()[:48]


# ---------------------------------------------------------------------------
# Reconciliation types
# ---------------------------------------------------------------------------

@dataclass
class DriftItem:
    """Single position that differs between broker truth and internal store."""
    symbol: str
    broker_qty: float
    internal_qty: float
    description: str


@dataclass
class ReconciliationReport:
    """Summary of one reconciliation cycle."""
    timestamp: datetime
    drift_items: list[DriftItem] = field(default_factory=list)
    broker_positions_count: int = 0
    internal_positions_count: int = 0
    error: Optional[str] = None

    @property
    def has_drift(self) -> bool:
        return bool(self.drift_items)

    @property
    def ok(self) -> bool:
        return not self.has_drift and self.error is None


# ---------------------------------------------------------------------------
# OrderManager
# ---------------------------------------------------------------------------

class OrderManager:
    """
    Submit orders idempotently, gate every intent through kill switch + risk
    gate, and reconcile broker ↔ internal state.

    Parameters
    ----------
    broker : BrokerBase
        Any BrokerBase implementation (AlpacaBroker, MockBroker, …).
    dry_run : bool
        When True every order intent is logged but not submitted.
    risk_gate : PreTradeRiskGate | None
        Pre-trade risk gate.  Pass ``None`` to skip risk checking (default).
    kill_switch : GlobalKillSwitch | None
        Kill-switch instance.  Defaults to a fresh ``GlobalKillSwitch()``
        pointing at ``settings.OUTPUT_DIR / "KILL_SWITCH"``.
    max_retries : int
        Number of additional attempts after a transient error (default 1).
    retry_delay_seconds : float
        Seconds to wait between retry attempts (default 2.0).
    alert_webhook_url : str | None
        Slack/Discord incoming webhook; sourced from settings if None.
    """

    def __init__(
        self,
        broker: BrokerBase,
        *,
        dry_run: bool = False,
        risk_gate: Optional[PreTradeRiskGate] = None,
        kill_switch: Optional[GlobalKillSwitch] = None,
        max_retries: int = 1,
        retry_delay_seconds: float = 2.0,
        alert_webhook_url: Optional[str] = None,
    ) -> None:
        self._broker = broker
        self._dry_run = dry_run
        self._risk_gate = risk_gate
        self._kill_switch = kill_switch or GlobalKillSwitch()
        self._max_retries = max_retries
        self._retry_delay = retry_delay_seconds
        self._alert_url = alert_webhook_url or getattr(settings, "ALERT_WEBHOOK_URL", None)
        # Set of client_order_ids already submitted this process lifetime.
        self._submitted: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit_order_with_idempotency(
        self,
        intent: OrderIntent,
        *,
        timestamp: Optional[datetime] = None,
        risk_context: Optional[RiskContext] = None,
    ) -> OrderResult:
        """Submit intent with kill-switch gate, pre-trade risk gate, and dedup.

        Order of operations
        -------------------
        1. Kill-switch check (raises ``KillSwitchActiveError`` if active).
        2. Derive ``client_order_id``; short-circuit if already submitted.
        3. Pre-trade risk gate (returns ERROR result if any check fails).
        4. ``_submit_with_retry`` → broker.

        Parameters
        ----------
        intent : OrderIntent
            The order to submit.  ``client_order_id`` is populated here.
        timestamp : datetime | None
            Wall-clock time for idempotency bucketing and rate-limit tracking.
        risk_context : RiskContext | None
            Per-call state for the risk gate.  Required when a risk gate is
            injected; silently skipped when no gate is configured.
        """
        # 1. Kill-switch gate — check before any state changes.
        if self._kill_switch.is_active():
            reason = self._kill_switch.reason()
            logger.critical(
                "ORDER BLOCKED — global kill switch is ACTIVE. Reason: %s", reason
            )
            raise KillSwitchActiveError(
                f"Global kill switch is active — all order submission blocked. "
                f"Reason: {reason or '(none)'}"
            )

        # 2. Idempotency: derive deterministic client_order_id.
        coid = make_client_order_id(
            intent.strategy_id,
            intent.symbol,
            intent.side.value,
            intent.qty,
            timestamp=timestamp,
        )
        intent.client_order_id = coid
        intent.dry_run = self._dry_run

        if coid in self._submitted:
            logger.warning(
                "Idempotency: client_order_id %s already submitted; skipping duplicate.",
                coid,
            )
            return OrderResult(
                client_order_id=coid,
                broker_order_id=None,
                status=OrderStatus.ACCEPTED,
            )

        # 3. Pre-trade risk gate (only if a gate was injected).
        if self._risk_gate is not None and risk_context is not None:
            gate_passed, gate_results = self._risk_gate.run_all(intent, risk_context)
            if not gate_passed:
                failing = next(r for r in gate_results if not r.passed)
                return OrderResult(
                    client_order_id=coid,
                    broker_order_id=None,
                    status=OrderStatus.ERROR,
                    error_message=f"PRE-TRADE GATE [{failing.check_name}]: {failing.reason}",
                )

        # 4. Submit (with optional retry on transient ERROR).
        result = await self._submit_with_retry(intent)

        if result.status != OrderStatus.ERROR:
            self._submitted.add(coid)
            logger.info(
                "Order accepted: %s %s x %.4f | coid=%s broker_id=%s",
                intent.side.value,
                intent.symbol,
                intent.qty,
                coid,
                result.broker_order_id,
            )
        else:
            logger.error(
                "Order FAILED after retries: %s %s x %.4f | coid=%s | %s",
                intent.side.value,
                intent.symbol,
                intent.qty,
                coid,
                result.error_message,
            )

        return result

    async def reconcile_state(
        self,
        transactions_store,  # TransactionsStore — avoid circular import
    ) -> ReconciliationReport:
        """
        Compare broker ground truth against internal TransactionsStore.

        Detects:
        - Positions the broker holds that our store shows as flat (orphaned fills).
        - Positions our store shows as open that the broker no longer holds.
        - Quantity mismatches.

        Logs CRITICAL on any drift and fires the webhook alert if configured.
        Returns a ``ReconciliationReport`` regardless of drift — never raises.
        """
        report = ReconciliationReport(
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None)
        )
        try:
            broker_positions = await self._broker.get_open_positions()
            broker_map: dict[str, float] = {
                p.symbol: p.qty for p in broker_positions
            }
            report.broker_positions_count = len(broker_map)

            try:
                open_df = transactions_store.open_trades_df()
                internal_map: dict[str, float] = {}
                if not open_df.empty:
                    for sym, grp in open_df.groupby("symbol"):
                        internal_map[str(sym).upper()] = float(grp["shares"].sum())
            except Exception as ts_err:
                logger.warning("reconcile_state: could not read TransactionsStore: %s", ts_err)
                internal_map = {}

            report.internal_positions_count = len(internal_map)
            all_symbols = set(broker_map) | set(internal_map)

            for sym in sorted(all_symbols):
                bq = broker_map.get(sym, 0.0)
                iq = internal_map.get(sym, 0.0)
                if abs(bq - iq) > 1e-4:
                    item = DriftItem(
                        symbol=sym,
                        broker_qty=bq,
                        internal_qty=iq,
                        description=(
                            f"broker={bq:.4f} internal={iq:.4f} "
                            f"delta={bq-iq:+.4f}"
                        ),
                    )
                    report.drift_items.append(item)
                    logger.critical(
                        "RECONCILIATION DRIFT: %s — %s",
                        sym,
                        item.description,
                    )

            if report.has_drift:
                await self._send_alert(report)
            else:
                logger.info(
                    "Reconciliation OK: %d broker positions, %d internal positions.",
                    report.broker_positions_count,
                    report.internal_positions_count,
                )

        except Exception as exc:
            report.error = str(exc)
            logger.error("reconcile_state crashed: %s", exc, exc_info=True)

        return report

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _submit_with_retry(self, intent: OrderIntent) -> OrderResult:
        # Dry-run interception at manager level so MockBroker tests also see the guard.
        if intent.dry_run:
            logger.info(
                "[DRY-RUN] Would submit %s %s x %.4f @ %s (strategy=%s, coid=%s)",
                intent.side.value.upper(),
                intent.symbol,
                intent.qty,
                intent.limit_price or "MARKET",
                intent.strategy_id,
                intent.client_order_id,
            )
            return OrderResult(
                client_order_id=intent.client_order_id or "",
                broker_order_id=None,
                status=OrderStatus.ACCEPTED,
                submitted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )

        last_result: Optional[OrderResult] = None
        for attempt in range(self._max_retries + 1):
            result = await self._broker.submit_order(intent)
            if result.status != OrderStatus.ERROR:
                return result
            last_result = result
            if attempt < self._max_retries:
                logger.warning(
                    "submit_order attempt %d failed (coid=%s); retrying in %.1fs: %s",
                    attempt + 1,
                    intent.client_order_id,
                    self._retry_delay,
                    result.error_message,
                )
                await asyncio.sleep(self._retry_delay)
        return last_result  # type: ignore[return-value]

    async def _send_alert(self, report: ReconciliationReport) -> None:
        """POST a JSON alert to the configured webhook URL (Slack/Discord)."""
        if not self._alert_url:
            return
        try:
            import urllib.request

            lines = [
                f"*InvestYo RECONCILIATION DRIFT* — {report.timestamp.isoformat()}",
                f"Broker positions: {report.broker_positions_count}",
                f"Internal positions: {report.internal_positions_count}",
            ] + [f"• {d.symbol}: {d.description}" for d in report.drift_items]
            payload = json.dumps({"text": "\n".join(lines)}).encode()
            req = urllib.request.Request(
                self._alert_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            logger.info("Drift alert sent to webhook.")
        except Exception as exc:
            logger.warning("Failed to send drift alert: %s", exc)
