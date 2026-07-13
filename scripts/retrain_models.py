"""
InvestYo Quant Platform - Automated Walk-Forward Retraining Orchestrator
========================================================================
THE single, standalone, scheduled entry point that retrains every production ML
model and refreshes ``ml/registry.yaml`` — the job the registry header long
*claimed* existed ("Updated automatically by the monthly retraining job ...")
but which was never actually written until now.

What it does
------------
1. Retrains the LGBM cross-sectional ranker via
   ``scripts.train_lgbm.run_training(...)``.
2. For each signal in ``ml.meta_bootstrap.META_LABELED_SIGNAL_IDS``, retrains
   its MetaLabeler via ``scripts.train_meta_labelers.train_signal(...)``.
3. Reads back ``ml.registry_io.load_registry()`` and prints a concise per-model
   summary (trained_date, cpcv_dsr, pbo, deployable).

Honest gating (do NOT re-implement here)
----------------------------------------
The ``deployable`` flag is re-derived inside ``update_model_metrics`` (called by
each trainer) from the run's real CPCV DSR/PBO via ``compute_deployable`` — the
single deployability gate (DSR > 0.95 AND PBO < 0.5). This orchestrator NEVER
sets, overrides, or loosens that gate. A model that honestly fails its gate is
reported ``deployable: false`` and is NOT treated as an error.

Dead-letter resilience (CONSTRAINT #6)
--------------------------------------
Each model's retraining is wrapped in its own try/except: one model raising an
exception is logged, recorded, and does NOT abort the others — every remaining
model is still retrained.

Exit code
---------
- ``0`` on completion, **even if some models are non-deployable** (failing a
  validation gate is an expected, honest outcome — not an error).
- Non-zero **only when a model's retraining raised a hard exception** (e.g. a
  training crash). Non-deployability alone never produces a non-zero exit.

Where it runs
-------------
This is an EXPENSIVE, standalone, scheduled job. It must NEVER run inside
``main.py`` / ``main_orchestrator.py`` advisory cycles. It is scheduled monthly
via the launchd plist ``scripts/com.investyo.monthly-retrain.plist``
(``python -m scripts.retrain_models``).

CLI
---
    python -m scripts.retrain_models [--offline] [--tickers T,T,...]
                                     [--signals S,S,...] [--log-level LEVEL]
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Repo-root import shim so `python scripts/retrain_models.py` works from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ml.meta_bootstrap import META_LABELED_SIGNAL_IDS  # noqa: E402
from ml.registry_io import load_registry  # noqa: E402
from scripts.train_lgbm import _DEFAULT_TICKERS, run_training  # noqa: E402
from scripts.train_meta_labelers import train_signal  # noqa: E402

logger = logging.getLogger("ML.RetrainModels")

_LGBM_MODEL_KEY = "lgbm_ranker"


@dataclass
class ModelResult:
    """Per-model outcome of a retraining attempt (bookkeeping only).

    ``ok`` is ``False`` ONLY when the trainer raised a hard exception — NOT when
    a model merely failed its (honest) deployability gate.
    """

    model_key: str
    ok: bool
    error: Optional[str] = None


@dataclass
class RetrainReport:
    results: List[ModelResult] = field(default_factory=list)

    @property
    def crashed(self) -> List[ModelResult]:
        return [r for r in self.results if not r.ok]

    @property
    def any_crash(self) -> bool:
        return len(self.crashed) > 0


def retrain_all(
    *,
    tickers: Optional[List[str]] = None,
    signals: Optional[List[str]] = None,
    offline: bool = False,
    registry_path: Optional[Path] = None,
) -> RetrainReport:
    """Retrain the LGBM ranker + every configured meta-labeler.

    Each model is trained inside its own try/except so one crash never aborts
    the rest (dead-letter resilience). Returns a :class:`RetrainReport` whose
    ``any_crash`` flag drives the process exit code. This function itself never
    raises for a per-model failure — it records it and continues.
    """
    report = RetrainReport()

    lgbm_tickers = tickers if tickers else list(_DEFAULT_TICKERS)
    signal_ids = signals if signals else list(META_LABELED_SIGNAL_IDS)

    # ---- 1. LGBM cross-sectional ranker -------------------------------------
    logger.info("Retraining LGBM ranker (%d tickers, offline=%s) ...",
                len(lgbm_tickers), offline)
    try:
        summary = run_training(
            lgbm_tickers,
            offline=offline,
            registry_path=registry_path,
        )
        logger.info(
            "LGBM ranker retrained: dsr=%s pbo=%s n_train=%s deployable=%s",
            summary.get("dsr"), summary.get("pbo"),
            summary.get("n_train"), summary.get("deployable"),
        )
        report.results.append(ModelResult(_LGBM_MODEL_KEY, ok=True))
    except Exception as exc:  # noqa: BLE001 — dead-letter: record + continue
        logger.error("LGBM ranker retraining CRASHED (%s) — continuing.", exc,
                     exc_info=True)
        report.results.append(ModelResult(_LGBM_MODEL_KEY, ok=False, error=str(exc)))

    # ---- 2. Meta-labelers, one per configured signal ------------------------
    for signal_id in signal_ids:
        model_key = f"meta_labeler_{signal_id}"
        logger.info("Retraining meta-labeler %r ...", signal_id)
        try:
            # train_signal is itself dead-letter safe (returns None on skip),
            # but we still guard against a hard raise here.
            # NOTE: train_signal() does not accept a registry_path override (its
            # meta_labeler_<id> row is written to the module-default registry).
            # We only thread --registry-path into the LGBM trainer + summary read.
            path = train_signal(
                signal_id,
                force_synthetic=offline,
                update_registry=True,
            )
            if path is None:
                logger.warning(
                    "Meta-labeler %r not retrained (insufficient data / skipped) "
                    "— not an error.", signal_id,
                )
            else:
                logger.info("Meta-labeler %r retrained → %s", signal_id, path)
            report.results.append(ModelResult(model_key, ok=True))
        except Exception as exc:  # noqa: BLE001 — dead-letter: record + continue
            logger.error("Meta-labeler %r retraining CRASHED (%s) — continuing.",
                         signal_id, exc, exc_info=True)
            report.results.append(ModelResult(model_key, ok=False, error=str(exc)))

    return report


def _print_summary(report: RetrainReport, registry_path: Optional[Path] = None) -> None:
    """Print a concise per-model summary read back from the registry."""
    registry = load_registry(registry_path)
    models = registry.get("models", {}) if isinstance(registry, dict) else {}

    print("\n=== Retraining Summary ===")
    for res in report.results:
        entry = models.get(res.model_key, {})
        if res.ok:
            print(
                f"  {res.model_key}: "
                f"trained_date={entry.get('trained_date')} "
                f"dsr={entry.get('cpcv_dsr')} "
                f"pbo={entry.get('pbo')} "
                f"deployable={entry.get('deployable')}"
            )
        else:
            print(f"  {res.model_key}: CRASHED ({res.error})")

    n_ok = sum(1 for r in report.results if r.ok)
    n_crash = len(report.crashed)
    n_deployable = sum(
        1 for r in report.results
        if r.ok and models.get(r.model_key, {}).get("deployable") is True
    )
    print(
        f"\n  {n_ok}/{len(report.results)} model(s) retrained without crashing; "
        f"{n_deployable} deployable; {n_crash} crashed."
    )
    if n_crash:
        print("  Exit code will be non-zero because at least one model crashed.")


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Retrain all InvestYo ML models and refresh ml/registry.yaml.",
    )
    p.add_argument(
        "--offline", action="store_true",
        help="Use synthetic / deterministic panels (no network) — for CI.",
    )
    p.add_argument(
        "--tickers", type=str, default=None,
        help="Comma-separated ticker universe for the LGBM ranker "
             "(default: scripts.train_lgbm._DEFAULT_TICKERS).",
    )
    p.add_argument(
        "--signals", type=str, default=None,
        help="Comma-separated subset of meta-labeled signal_ids to retrain "
             "(default: all in META_LABELED_SIGNAL_IDS).",
    )
    p.add_argument(
        "--registry-path", type=str, default=None,
        help="Override registry.yaml path (for tests).",
    )
    p.add_argument("--log-level", type=str, default="INFO")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers else None
    )
    signals = (
        [s.strip() for s in args.signals.split(",") if s.strip()]
        if args.signals else None
    )
    registry_path = Path(args.registry_path) if args.registry_path else None

    report = retrain_all(
        tickers=tickers,
        signals=signals,
        offline=args.offline,
        registry_path=registry_path,
    )
    _print_summary(report, registry_path)

    # Exit non-zero ONLY on a hard crash — a non-deployable model is expected.
    return 1 if report.any_crash else 0


if __name__ == "__main__":
    raise SystemExit(main())
