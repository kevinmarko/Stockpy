# RLHF Calibration Review Queue

**Status: shipped.** This document originally sketched a plan (routing to a
new `/rlhf-calibration` page, a KPI panel citing RL-training metrics like
"Reward Model Score"/"Policy KL Divergence", an optional AI chat panel).
What actually got built differs from that sketch in a few load-bearing ways,
documented below so a future reader doesn't go looking for a page or metric
that was deliberately not built this way. See this PR's diff for the full
implementation.

## What this is

A human operator reviews an AI trading agent's hypothetical **paper-trade
proposals** — a symbol, action, rationale, confidence, and technical context
snapshot — and submits a 1-5 star rating plus an optional corrective
comment. A 5-star rating can be exported to a JSONL supervised-fine-tuning
(SFT) dataset. Nothing here places a real order or touches real capital;
proposals are entirely separate from `TransactionsStore` (which backs real-
trade MAE/MFE evaluation) and from Alpaca paper-trading mode.

## Where it actually lives

- **Not a new route.** It's a "RLHF Review Queue" section nested inside the
  existing `/agentic` screen (`webapp/src/screens/AgenticTrading.tsx`,
  component `webapp/src/components/RlhfReviewQueue.tsx`). This repo already
  has an unrelated `/calibration` screen (a statistical reliability curve —
  conviction bins vs. realized win rate), so naming the new feature
  "Calibration" anywhere user-facing would have collided with it.
- **KPIs are the real, computable ones** — pending count, average human
  rating, rating distribution, auto-approved count, SFT-exported count —
  not the RL-training metrics originally sketched, which this platform
  doesn't and won't compute (no RL policy training exists here).
- **No chat panel.** Deferred as a separate follow-up.
- **No webapp "create proposal" form.** Proposals originate only from an AI
  agent, via a new MCP tool (`propose_paper_trade_for_review` in
  `investyo_mcp_server.py`) or directly via `POST /rlhf/proposals`. The
  webapp is review-only.

## Where the pieces are

| Layer | File |
|---|---|
| Store (SQLAlchemy, `rlhf_calibration_proposals` table) | `rlhf_calibration_store.py` |
| Dependency-light read helper | `pilots/rlhf_review_queue.py` |
| API endpoints | `api/pilots_api.py` (`GET /rlhf/summary`, `POST /rlhf/proposals`, `POST /rlhf/proposals/{id}/review`, `POST /rlhf/export-sft`) |
| Settings | `settings.py` (`RLHF_CALIBRATION_ENABLED`, `RLHF_CALIBRATION_CONFIDENCE_THRESHOLD`, `RLHF_CALIBRATION_AUTO_APPROVE_ENABLED`, `RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED`) |
| MCP tool | `investyo_mcp_server.py::propose_paper_trade_for_review` |
| Webapp | `webapp/src/components/RlhfReviewQueue.tsx`, wired into `AgenticTrading.tsx` |
| SFT export | `output/rlhf_sft_dataset.jsonl` (append-only, gitignored) |

`RLHF_CALIBRATION_ENABLED` defaults `True` (paper-only, no capital/execution
risk — ships active per this repo's 2026-08-03 admin-write-gate
convention). The two auto-* behaviors (skip human review above a confidence
threshold; auto-export a 5-star rating) default `False` — both change what
counts as "reviewed"/"exported" without a human in the loop, so they stay
opt-in.
