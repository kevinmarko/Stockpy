"""
execution/order_manager.py
===========================
Stateful order submission layer sitting between the signal/sizing pipeline and
the broker adapter.

Responsibilities
----------------
1. **Kill-switch gate** — if the global kill switch is active,
   ``submit_order_with_idempotency`` raises ``KillSwitchActiveError`` BEFORE
   the dedup check and BEFORE any broker call, making it impossible to bypass.
   A SECOND check runs inside ``_submit_with_retry``, immediately before each
   real broker call (after the LeakyBucketQueue wait / retry backoff sleep),
   so activating the switch WHILE an order is waiting in the queue or between
   retries still blocks it -- the entry-time check alone cannot see a switch
   flipped during that wait.

2. **Idempotency** — every ``OrderIntent`` is assigned a deterministic
   ``client_order_id`` derived from (strategy_id, timestamp_bucket, symbol,
   side, qty).  Submitting the same intent twice within the same bucket
   produces the same ID; the broker deduplicates it, and the local
   ``_submitted`` set prevents even a second network call.

3. **Pre-trade risk gate** — every OrderIntent must pass
   ``PreTradeRiskGate.run_all()`` after the dedup check but before the broker.
   Returns an ``ERROR`` OrderResult if any check fails (does not raise).

4. **Transient-error retry** — single linear back-off retry (configurable)
   for transient broker errors before giving up.

5. **State reconciliation** — ``reconcile_state`` compares internal
   ``TransactionsStore`` active positions with the broker's ground truth every
   cycle, logging CRITICAL on any drift and optionally posting a webhook alert.

6. **Dry-run mode** — when ``dry_run=True`` (CLI flag ``--dry-run`` or
   ``settings.DRY_RUN``), intents are logged but never forwarded to the broker;
   returns synthetic OrderResult with status=ACCEPTED.

Kill-switch integration
-----------------------
``GlobalKillSwitch`` is injected at construction.  If ``None`` (default), a
fresh instance is constructed pointing at the default sentinel file.  Pass an
instance with a temp-dir path in tests that need to control the file.

Risk-gate integration
---------------------
``PreTradeRiskGate`` is injected at construction.  If ``None`` (default), the
gate is **skipped entirely** — safe default for backward-compat and tests that
only care about idempotency / reconciliation.  Pass an instance to enable.

Alerting
--------
Set ``ALERT_WEBHOOK_URL`` in ``.env`` to a Slack / Discord incoming-webhook
URL.  On reconciliation drift, an HTTP POST is sent with a JSON payload.
Alerting failures are logged but never crash the reconciliation.
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
from execution.leaky_bucket_queue import LeakyBucketQueue
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
    Submit orders through kill-switch gate → idempotency dedup → risk gate →
    broker, and reconcile broker ↔ internal state.

    Parameters
    ----------
    broker : BrokerBase
        Any BrokerBase implementation (AlpacaBroker, MockBroker, …).
    dry_run : bool
        When True every order intent is logged but not submitted.
    risk_gate : PreTradeRiskGate | None
        Pre-trade risk gate.  Pass ``None`` to skip (default for backward compat).
    kill_switch : GlobalKillSwitch | None
        Kill-switch instance.  Defaults to ``GlobalKillSwitch()`` pointing at
        ``settings.OUTPUT_DIR / "KILL_SWITCH"``.
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
        # Prevents a double-call bug even when the broker's dedup window expires.
        self._submitted: set[str] = set()
        # Per-client_order_id lock making the "check _submitted, then submit,
        # then register in _submitted" sequence atomic across concurrent
        # asyncio callers. Without this, two concurrent calls for the SAME
        # coid can both observe "not yet submitted" before either one
        # registers it (the register only happens after the broker call,
        # which awaits) -- both then reach the broker. Locks are created
        # lazily via dict.setdefault, which is synchronous and therefore
        # race-free even without its own guard. Grows unboundedly across a
        # process lifetime, matching ``_submitted``'s own documented growth.
        self._submit_locks: dict[str, asyncio.Lock] = {}
        # Rate-limiting queue — sheds low-priority (BUY) requests under load;
        # high-priority (SELL / stop-loss) are allowed to wait for a token.
        self._queue: LeakyBucketQueue = LeakyBucketQueue(
            capacity=200,      # Alpaca ~200 req/min on the free IEX feed
            refill_rate=3.33,  # 200 tokens / 60 seconds
        )


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
        """Submit intent through kill switch → dedup → risk gate → broker.

        Order of operations
        -------------------
        1. Kill-switch check — raises ``KillSwitchActiveError`` if active.
        2. Derive ``client_order_id``; return ACCEPTED early if already submitted.
           Steps 2-4 run inside a per-coid ``asyncio.Lock`` so two concurrent
           calls for the same intent can't both pass the dedup check before
           either registers it — see the lock's own inline comment.
        3. Pre-trade risk gate — returns ERROR result if any check fails.
        4. ``_submit_with_retry`` → rate-limit gate + broker (see that method's
           docstring for why the LeakyBucketQueue gate lives there, not here;
           also re-checks the kill switch immediately before each real broker
           call, closing the TOCTOU window a queue wait or retry sleep opens).

        Parameters
        ----------
        intent : OrderIntent
            The order to submit.  ``client_order_id`` is populated here.
        timestamp : datetime | None
            Wall-clock time for idempotency bucketing and rate-limit tracking.
        risk_context : RiskContext | None
            Per-call state for the risk gate.  Silently skipped when no gate
            is configured (``self._risk_gate is None``).
        """
        # 1. Kill-switch gate — BEFORE dedup so the sentinel is impossible to bypass.
        if self._kill_switch.is_active():
            reason = self._kill_switch.reason()
            logger.critical(
                "ORDER BLOCKED — global kill switch ACTIVE. Reason: %s", reason
            )
            raise KillSwitchActiveError(
                f"Global kill switch is active — all order submission blocked. "
                f"Reason: {reason or '(none)'}"
            )

        # 2. Idempotency dedup.
        coid = make_client_order_id(
            intent.strategy_id,
            intent.symbol,
            intent.side.value,
            intent.qty,
            timestamp=timestamp,
        )
        intent.client_order_id = coid
        intent.dry_run = self._dry_run

        # The check-(risk-gate)-submit-register sequence below must be
        # atomic per coid: two concurrent calls for the SAME client_order_id
        # could otherwise both observe "not yet submitted" before either one
        # registers it (registration only happens after the broker call,
        # which awaits and yields control) -- both would then reach the
        # broker, defeating idempotency. A per-coid asyncio.Lock serializes
        # the whole sequence for a given coid; a concurrent call for a
        # DIFFERENT coid is unaffected (each coid gets its own lock).
        lock = self._submit_locks.setdefault(coid, asyncio.Lock())
        async with lock:
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

            # 3. Pre-trade risk gate (only when a gate and context were injected).
            if self._risk_gate is not None and risk_context is None:
                logger.warning(
                    "Risk gate is configured but no risk_context was supplied for %s %s x %.4f "
                    "(coid=%s) -- ALL pre-trade risk checks are being silently skipped for this "
                    "order. Pass risk_context= to submit_order_with_idempotency to enable them.",
                    intent.side.value, intent.symbol, intent.qty, coid,
                )
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

            # Build internal active-position map from TransactionsStore.
            # Each open trade (no exit_ts) contributes qty by symbol.
            try:
                open_df = transactions_store.open_trades_df()
                internal_map: dict[str, float] = {}
                if not open_df.empty:
                    # shares column; group by symbol
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
        """Dry-run check, then the rate-limit gate + retry loop around the real broker call.

        The LeakyBucketQueue gate lives HERE, not in submit_order_with_idempotency,
        and is checked immediately before EACH broker.submit_order attempt (not
        once per intent): applying it once per intent meant a dry-run consumed a
        token for zero real API calls, while a retried intent made multiple real
        broker calls off a single token check -- the limiter neither protected
        the broker's actual request volume under a retry storm nor freed the
        token budget a dry-run never spent.
        """
        # Dry-run interception at manager level so MockBroker tests also see the
        # guard. No token is consumed -- no real broker request is ever made.
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
            from datetime import timezone
            return OrderResult(
                client_order_id=intent.client_order_id or "",
                broker_order_id=None,
                status=OrderStatus.ACCEPTED,
                submitted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )

        # SELL / STOP orders (side contains 'sell') are high-priority (1) and
        # will spin-wait for the next available token. BUY orders are
        # low-priority (0) and are shed (dropped) when utilization > 80 %.
        order_side = intent.side.value.lower() if hasattr(intent.side, "value") else str(intent.side).lower()
        is_sell = "sell" in order_side
        priority = 1 if is_sell else 0

        last_result: Optional[OrderResult] = None
        for attempt in range(self._max_retries + 1):
            # Gated immediately before each real broker call, using the async
            # await_or_shed (asyncio.sleep spin-wait), not the sync wait_or_shed
            # (time.sleep) -- this loop runs inside the event loop, and a
            # blocking time.sleep here would stall every other coroutine
            # sharing it for the duration of the wait.
            if not await self._queue.await_or_shed(priority=priority):
                logger.warning(
                    "LeakyBucketQueue: shedding low-priority %s order for %s (API rate limit > 80%%).",
                    order_side, intent.symbol,
                )
                return OrderResult(
                    client_order_id=intent.client_order_id or "",
                    broker_order_id=None,
                    status=OrderStatus.ERROR,
                    error_message="Rate-limit load-shed: API utilization above threshold. Retry later.",
                )

            # Second kill-switch check, immediately before the actual broker
            # call -- the LeakyBucketQueue wait above (or a prior retry's
            # backoff sleep) can take seconds, during which the kill switch
            # could be activated AFTER the entry-time check in
            # submit_order_with_idempotency already passed. Without this,
            # the module docstring's "impossible to bypass" claim would be
            # false for any order that spent time waiting in the queue or
            # between retries.
            if self._kill_switch.is_active():
                reason = self._kill_switch.reason()
                logger.critical(
                    "ORDER BLOCKED — global kill switch ACTIVE (detected before broker "
                    "call, mid-submission). Reason: %s", reason,
                )
                raise KillSwitchActiveError(
                    f"Global kill switch is active — all order submission blocked. "
                    f"Reason: {reason or '(none)'}"
                )

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
        """Dispatch a reconciliation-drift alert via two independent paths.

        (1) The hardened multi-channel dispatcher
            (``observability.alerts.send_alert``) at CRITICAL severity — its
            console + file channels are always-on, plus discord/slack/email
            when configured. This is the primary path and fires even when no
            ``ALERT_WEBHOOK_URL`` is set.
        (2) The legacy single ``ALERT_WEBHOOK_URL`` POST, kept for backward
            compatibility.

        The two paths are isolated so a failure in one never suppresses the
        other, and this method never raises (dead-letter safe — reconciliation
        must complete regardless of alert-channel health).
        """
        lines = [
            f"*InvestYo RECONCILIATION DRIFT* — {report.timestamp.isoformat()}",
            f"Broker positions: {report.broker_positions_count}",
            f"Internal positions: {report.internal_positions_count}",
        ] + [f"• {d.symbol}: {d.description}" for d in report.drift_items]
        message = "\n".join(lines)

        # (1) Multi-channel dispatcher. Imported lazily (repo convention) to
        #     avoid any import cycle between execution/ and observability/.
        #     send_alert() is itself dead-letter safe, but we still guard the
        #     import + call so a broken observability module can never crash
        #     reconcile_state.
        try:
            from observability.alerts import send_alert as _multichannel_alert

            _multichannel_alert(
                "CRITICAL",
                message,
                extra={
                    "type": "reconciliation_drift",
                    "broker_positions": report.broker_positions_count,
                    "internal_positions": report.internal_positions_count,
                    "drift": [
                        {
                            "symbol": d.symbol,
                            "broker_qty": d.broker_qty,
                            "internal_qty": d.internal_qty,
                            "description": d.description,
                        }
                        for d in report.drift_items
                    ],
                },
            )
        except Exception as exc:
            logger.warning("Multi-channel drift alert dispatch failed: %s", exc)

        # (2) Legacy webhook POST — unchanged back-compat behavior.
        if not self._alert_url:
            return
        try:
            import urllib.request

            payload = json.dumps({"text": message}).encode()
            req = urllib.request.Request(
                self._alert_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            # Bandit B310: `self._alert_url` is settings.ALERT_WEBHOOK_URL,
            # an operator-set config value (see __init__ above), never raw
            # external input.
            urllib.request.urlopen(req, timeout=5)  # nosec B310
            logger.info("Drift alert sent to webhook.")
        except Exception as exc:
            logger.warning("Failed to send drift alert: %s", exc)
