---
name: robinhood-execution
description: >-
  Execute the Stockpy advisory platform's gated order queue against the Robinhood
  Trading MCP, PAPER-FIRST. Use when the operator asks to review or place the
  pending Robinhood trades, run the execution queue, or act on the execution
  queue (`execution_queue.json`). Always previews via review_equity_order; only
  places real orders in `live` mode with explicit per-trade human confirmation —
  in Robinhood trade-approval mode that confirmation is the operator's tap in
  the Robinhood app.
---

# Robinhood Execution (paper-first, human-gated)

This skill is the **only** actor permitted to call the Robinhood Trading MCP
write tools. The headless Stockpy pipeline (the orchestrator daemon, the
queue writer since the 2026-10-12 cutover) cannot call MCP tools; it
only writes a gated, dry-run proposed-order queue to
`$OUTPUT_DIR/execution_queue.json` (via `execution/queue_builder.py`) —
see Prerequisites step 1 below for how to resolve `$OUTPUT_DIR`, the
platform's configured output directory (`settings.OUTPUT_DIR`, **not** a
literal `output/` subdirectory of this checkout — this repo runs many
concurrent git worktrees, and a literal relative path resolves against
whichever worktree you happen to be in, not the real shared location). You
read that queue and turn eligible intents into MCP calls — **previewing
always, placing only under strict conditions.**

You may arrive here two ways: the operator asks you to run this skill, or
`execution/queue_builder.py` pushed them an ntfy notification (if
`ALERT_NTFY_TOPIC` is configured) because the queue gained a new or newly-placeable
intent since the last cycle. Either way, treat this as a **conversation, not a
script**: narrate what you're seeing and why in plain English, and actively
invite the operator to ask questions, request more detail (their existing
position, recent earnings, why the gate blocked or cleared something), or
redirect before you move on. The procedure and hard stops below are the
non-negotiable safety rails; how you talk through them with the operator is
not — read them a checklist only if they ask for one.

## Prerequisites (verify before doing anything else)

1. **Resolve the output directory once.** Every artifact this skill reads or
   writes — the execution queue, the kill-switch sentinel, the placed-intent
   ledger, the receipts ledger — lives under the platform's configured
   `settings.OUTPUT_DIR` (default `~/.stockpy_local/output`, but
   operator-configurable via `LOCAL_DATA_ROOT`/`OUTPUT_DIR` in `.env`) —
   **never** a literal `output/` subdirectory of this checkout, which may be
   stale, empty, or a different worktree's leftovers entirely. Run this once,
   at the very start of the session, and reuse the result (called
   `$OUTPUT_DIR` throughout the rest of this skill) for every reference
   below — do not re-derive it per file or per step:
   ```bash
   python3 -c "from settings import settings; print(settings.OUTPUT_DIR)"
   ```
   If that errors (e.g. a missing dependency because the active `python3`
   isn't this repo's own environment), retry with
   `.venv/bin/python3 -c "from settings import settings; print(settings.OUTPUT_DIR)"`
   if this worktree has its own `.venv`, or point at another checkout's
   `.venv/bin/python` — the value doesn't depend on which worktree computed
   it, since `settings.OUTPUT_DIR` is a fixed, operator-configured path, not
   worktree-relative. If you still cannot resolve it, **stop and ask the
   operator** for the directory rather than guessing or falling back to a
   bare `output/` path — the whole point of this step is that a repo-relative
   guess can silently read a stale or empty queue instead of the real one.

   `$OUTPUT_DIR` now names:
   - `$OUTPUT_DIR/execution_queue.json` — the gated queue (step 3 below).
   - `$OUTPUT_DIR/execution_placed.jsonl` — the placed-intent ledger (step 4).
   - `$OUTPUT_DIR/execution_receipts.jsonl` — the outcome audit trail.
   - `$OUTPUT_DIR/execution_approvals.jsonl` — the trade-approval outcome log
     (one line per observed approval state; the last line per `approval_id`
     wins). Written only through `execution/receipts_store.py`'s
     `append_approval_event`.
   - `$OUTPUT_DIR/agentic/<run_id>/preflight.json` — the Portfolio & Risk
     preflight for one queue (written by `execution/agentic_preflight.py`, see
     step 4b).
   - `$OUTPUT_DIR/KILL_SWITCH` — the kill-switch sentinel file.
2. The `robinhood-trading` MCP server is connected (tools `review_equity_order`,
   `place_equity_order`, `get_accounts`, `get_portfolio`, `get_equity_positions`,
   `get_equity_quotes`, `get_equity_orders` are available). If not, tell the
   operator to run `claude mcp add robinhood-trading --transport http
   https://agent.robinhood.com/mcp/trading` and authenticate via `/mcp`. Stop.
3. **Find out who writes the queue, and refresh it.** Read the writer mode once:
   ```bash
   python3 -c "from settings import settings; print(settings.DAEMON_AGENTIC_QUEUE_MODE)"
   ```
   (same `.venv/bin/python3` fallback as step 1).
   - **`primary`** (the live setting since 2026-10-12) — the orchestrator
     daemon writes the queue. `main.py` was archived to `legacy/` (step 5.5), so
     never tell the operator to run it for a fresh queue. Before loading the queue, trigger a
     daemon cycle and wait for it to finish:
     ```bash
     python3 - <<'EOF'
     import time
     from shared import daemon_client as dc
     r = dc.trigger_run(timeout=10)
     # 409 = a cycle is already running: wait for that one instead.
     run_id = r.run_id if r.ok else (r.existing_run_id if r.error == "already_running" else None)
     if run_id is None:
         raise SystemExit(f"could not trigger a daemon cycle: {r.error}")
     deadline = time.time() + 45 * 60
     while time.time() < deadline:
         s = dc.get_run_status(run_id) or {}
         if s.get("state") in ("succeeded", "failed"):
             print(run_id, s.get("state"), s.get("error"))
             break
         time.sleep(15)
     else:
         raise SystemExit(f"daemon cycle {run_id} still running after 45 min")
     EOF
     ```
     Handle the result plainly: `kill_switch_active` (HTTP 423) → the
     platform is paused, stop (hard stop below). `network_error` /
     `unavailable` → the daemon isn't running; tell the operator to start it
     (`launch_webapp.command` or the `com.investyo.stack` service) and stop.
     `unauthorized` / `command_disabled` → `ORCHESTRATOR_DAEMON_TOKEN` is
     missing or wrong in `.env`; tell the operator and stop. A cycle that ends
     `failed` → say so and do not place. A cycle that ends `succeeded` does not
     guarantee a new queue (a cycle writes nothing when it ran on synthetic
     data -- no `FRED_API_KEY`, or a price fetch that came back empty; its run
     progress message then starts `SYNTHETIC DATA` -- when the advisory step
     failed, or when no BUY/SELL cleared the conviction floor; if the queue's
     reference prices still look implausible next to the live quotes
     `review_equity_order` returns, say so and stop), so still apply the
     freshness hard stop below to the queue you then read.
   - **`off` or `shadow`** — the daemon is **not** the queue writer. That is
     either a rollback (`docs/RUNBOOK.md` §3.15: the operator reloaded the
     08:45 `com.investyo.daily-advisory` job) or a misconfiguration. **Stop
     and ask the operator which.** Do not trigger `POST /run` (in these modes
     it does not write the real queue) and never run `main.py` (it is archived;
     a rollback means the operator reverted the step 5.5 PR first, per
     RUNBOOK §3.15). If the operator confirms a rollback, you may continue with the
     queue the 08:45 job last wrote, under every hard stop below; if it is
     stale, stop and say when the next 08:45 run will refresh it.

   Then check that `$OUTPUT_DIR/execution_queue.json` exists. If missing, the
   platform is in `ROBINHOOD_EXECUTION_MODE=off` (or hasn't run). Tell the
   operator to set the mode to `review` or `live` in `.env` and refresh the
   queue as above (a daemon cycle in `primary`; in a rollback, the next 08:45
   run). Stop.
4. `$OUTPUT_DIR/execution_placed.jsonl` is the append-only **placed-intent ledger**
   (may not exist yet — that just means nothing has been placed). Each line is
   one JSON record:
   `{"ts","dedup_key","symbol","side","qty","target_notional","client_order_id","mcp_order_id","approval_id","ref_id","run_id"}`,
   where `dedup_key = "YYYY-MM-DD:SYMBOL:SIDE"` in **UTC**. In trade-approval
   mode a ledger line means **submitted for the operator's approval**, not
   executed; `approval_id` links it to `$OUTPUT_DIR/execution_approvals.jsonl`. You consult it for
   the idempotency check (step 5) and append to it after every successful
   placement. It is distinct from `$OUTPUT_DIR/execution_receipts.jsonl` (the
   broader reviewed/placed/skipped audit trail) — write BOTH on a placement.

## Hard stops (refuse and explain — do not proceed)

- `$OUTPUT_DIR/KILL_SWITCH` exists **OR** the queue's `kill_switch_active` is
  `true` → the platform is paused. Refuse all placement. (Deactivate with
  `python -m execution.kill_switch --deactivate` only on operator instruction.)
- The queue's `mode` is `off` → nothing to do.
- The queue's `generated_at` is more than ~30 minutes old → it is STALE. Refuse
  to place; offer to refresh it first (Prerequisites step 3: a daemon cycle via
  `POST /run` when `DAEMON_AGENTIC_QUEUE_MODE=primary`; in any other mode there
  is no refresh you may trigger, so stop). This rule is unchanged in `primary`: a daemon cycle that
  succeeded but left an older queue in place does not make that queue fresh.
- `get_accounts` does not show a dedicated **Agentic** account, or the operator
  has not confirmed which account is the agentic/execution account → refuse to
  place anything. Robinhood only allows agent orders in the separately-funded
  Agentic account; never operate against the main account.

## Robinhood trade-approval mode (how "confirmation" works)

The Agentic account can require the customer's manual approval for every trade
(`get_trade_approval_setting` → `human_must_approve_trades: true`). In that mode
`place_equity_order` does **not** execute anything: it sends the order to the
operator's Robinhood app, where **only they** can approve it. You can never
approve a trade; you can only decline a pending one, and only with the
operator's OK. That phone tap is a gate no agent can satisfy on the operator's
behalf, which is why it replaces the in-chat "place / skip / stop" question —
but only order by order, and only when Robinhood itself says so:

- **Approval path** — the order's `review_equity_order` response shows
  `customer_approval_required: true` **and** `order_checks` is empty. Submit it
  without asking in chat; the operator's tap in the app is the per-order
  confirmation. Still narrate it ("sending AAPL BUY 3 @ ≤ $191.20 to your phone
  for approval") and stop at once if the operator says skip or stop.
- **In-chat path** — anything else: `customer_approval_required` is false or
  absent, `order_checks` is non-empty, or the approval setting could not be
  read. The legacy rule applies unchanged: ask "place / skip / stop" for this
  order and wait for an explicit affirmative. When in doubt, use this path —
  it is the stricter one.

Never decide the path from memory or from an earlier call: Robinhood lets the
customer flip the setting at any time, so the per-order `review_equity_order`
response is the truth for that order.

## Unattended cycle (`/rh-agentic-cycle`)

When the `/rh-agentic-cycle` coordinator runs you as its Trader stage, nobody
may be at the keyboard. Everything in this skill still applies, with these
overrides (the coordinator passes them verbatim):

- Do not trigger a daemon run; use the queue as it is. If its `generated_at`
  is not the cycle's `queue_generated_at`, place nothing and report a failure.
- Place only on the **approval path**. Never ask in chat: an order that would
  need in-chat confirmation is skipped with the note
  `in_chat_confirmation_unavailable`. With trade approvals off, place nothing.
- Never call `decline_trade_approval` unattended; stale approvals go into the
  cycle's summary push for the operator.
- Record `run_id` in every placed record, check approvals once, and don't wait
  for the operator's tap.

## Procedure

1. **Load state and orient the operator.** Read `$OUTPUT_DIR/execution_queue.json`.
   Note `mode`, `kill_switch_active`, `max_notional_per_order`, and the
   `intents` list. Run the hard-stop checks above. Then, before touching any
   MCP tool, give the operator a short spoken overview — mode, how many
   intents, how many are `allow_place: true`, and the highest-conviction 1-2 —
   and pause for questions. Don't front-load every detail; let the operator
   pull on whatever they want to know more about.
2. **Confirm the account.** Call `get_accounts` / `get_portfolio`. Identify the
   Agentic account and confirm it with the operator. Show buying power. Once the
   operator confirms which account is the Agentic/execution account, **remember
   it for the rest of this session** — you don't need to re-run the discovery
   dance every intent. You still re-confirm ("placing into your Agentic account
   ‹…›, correct?") as part of the per-order confirm gate in step 5c before any
   placement; persistence saves the lookup, not the confirmation.
   Then read `get_trade_approval_setting` for that account and tell the
   operator plainly whether trade approvals are on (which path the orders will
   most likely take). If the call errors, say so and treat every order as the
   in-chat path.
   **Refresh earlier approvals.** List the approval ids still `pending` in the
   log:
   ```bash
   python3 -c "from execution.receipts_store import pending_approval_ids; print(pending_approval_ids())"
   ```
   If any, call `get_trade_approvals(account_number, approval_ids=[...])` and
   record every state you observe (step 6's recording recipe). An id the call
   does not return stays as last recorded — say it is unknown, don't guess.
3. **Preview every intent (ALWAYS), one at a time, narrated.** For each
   intent, call `review_equity_order` with its `symbol`, `side`, `order_type`,
   and quantity:
   - SELL intents come in two shapes:
     - **Full exit** — `qty` is a concrete number (the held share count). Sell
       exactly that quantity.
     - **Partial trim** — `qty: null` with a `target_notional` (a Pilot-follow
       rebalance-down). Resolve it exactly like a BUY: call `get_equity_quotes`
       for a live price, compute `qty = floor(target_notional / price)`, then
       **cap `qty` at the held share count** (`get_equity_positions`) so a trim can
       never oversell, and verify `qty > 0`. Preview only if the resolved qty is 0.
   - BUY intents carry `qty: null` and a `target_notional`. Call
     `get_equity_quotes` for a live price, compute
     `qty = floor(target_notional / price)`, and verify
     `qty * price <= max_notional_per_order` (and `> 0`). If
     `max_notional_per_order` is `0`, refuse to place (cap unset) — preview only.
   - **Order type.** An intent may carry `order_type` and `limit_offset_bps`.
     - `order_type: "market"` (or `order_type` absent, or `limit_offset_bps`
       `0`/absent) → a **market** order, exactly as today. Pass no limit price.
     - `order_type: "limit"` with a positive `limit_offset_bps` → a **limit**
       order. Resolve the limit price from the **live MCP quote at review time**
       (`get_equity_quotes`), never from the queue's snapshot price:
       - BUY:  `limit_price = quote * (1 + limit_offset_bps / 10000)` — you pay
         at most this; the offset is the max slippage you'll tolerate above the
         quote.
       - SELL: `limit_price = quote * (1 - limit_offset_bps / 10000)` — you
         receive at least this.
       Round the limit price to the venue's tick (2 decimals for equities), pass
       it to BOTH `review_equity_order` and `place_equity_order`, and state the
       resolved limit price aloud when you narrate the intent. For a BUY limit,
       size `qty` off the resolved `limit_price` (not the raw quote) so the
       `qty * limit_price <= max_notional_per_order` cap check stays honest.
   Walk the operator through it in your own words — symbol, side, qty, est.
   notional, Robinhood's pre-trade warnings, the queue's `conviction` and
   `rationale`, and (for a blocked intent) *why* `gate_reasons` says what it
   says. Then explicitly invite questions about this specific intent — e.g.
   their current position/cost basis (`get_equity_positions`), upcoming
   earnings (`get_earnings_calendar`), or a different size — and answer them
   using whatever read-only MCP tools help, before moving to the confirm gate
   in step 5. Don't rush past this into the next intent.
4. **Mode gate.**
   - `mode == "review"` → **STOP after previews.** This is the paper/dry-run
     stage. Never call `place_equity_order`. Summarise the previews and end.
   - `mode == "live"` → continue, but only for intents whose `allow_place` is
     `true`. Treat `allow_place: false` as preview-only and say why
     (`gate_reasons`).
4b. **Portfolio & Risk preflight (live only).** Before placing anything, get
   the preflight for THIS queue — the code-enforced check of every intent
   against the live Agentic account (cumulative buying power, concentration,
   notional cap, sellable shares, pending approvals, staleness, kill switch):
   - Run the `rh-risk-manager` agent (or follow its procedure yourself), which
     saves fresh `get_portfolio` / `get_equity_positions` / `get_equity_quotes`
     / `get_trade_approvals` output and runs `python3 -m execution.agentic_preflight`.
   - Load it with
     `python3 -c "import json; from execution.agentic_preflight import read_preflight_for_queue; print(json.dumps(read_preflight_for_queue(json.load(open('$OUTPUT_DIR/execution_queue.json')))))"`.
     It returns the preflight only if its `queue_generated_at` equals this
     queue's `generated_at`; anything else prints `null`.
   - **A preflight veto is final.** Place only intents whose verdict has
     `passed: true`, using the verdict's `qty` and `limit_price` (it sized them
     from the live quote and the account's real cash). Never place a vetoed
     intent, not even through the in-chat question — tell the operator the
     reason codes in plain words instead. `run_blockers` veto everything.
   - **No usable preflight** (`null`, unreadable, or it failed to run): say so,
     and treat every order as the **in-chat path** (step 5c), sizing it
     yourself as in step 3. A missing check makes the process stricter, never
     looser.
4c. **Thesis per order (narration only).** Load the Strategy Analyst's
   validated theses for this queue:
   `python3 -c "import json; from execution.agentic_research import load_run_artifacts; a=load_run_artifacts(json.load(open('$OUTPUT_DIR/execution_queue.json'))); t=(a['theses'] or {}).get('theses', {}); print(json.dumps({c: t[c] for c in a['valid_thesis_ids']}))"`.
   Only theses that pass `execution/agentic_research.py`'s validator are
   printed; every number in them is checked against its cited source.
   Before each order goes to the operator's phone, say its **thesis**,
   **invalidation** and any **flags** in one or two lines, and put the one-line
   thesis in the ntfy text. For an order with no valid thesis, say
   "thesis unavailable" and why (missing, or the validator rejected it). Never write a
   thesis yourself, and never quote a number that isn't in a validated thesis
   or the queue. This step changes nothing about which orders are placed. Only
   the preflight vetoes, and its opt-in check 10 (`--critical-gaps`) is how a
   missing or invalid thesis can block an order.
5. **Place (live only, one at a time, human-confirmed).** For each
   `allow_place: true` intent:
   a. Re-read `$OUTPUT_DIR/KILL_SWITCH`; if it now exists, abort the whole run.
      Skip any intent the preflight vetoed (step 4b). For a preflight-passed
      intent, call `review_equity_order` again with the preflight's `qty` and
      `limit_price` — that fresh response is the one step 5c reads.
   b. **Idempotency check.** Compute this intent's
      `dedup_key = "YYYY-MM-DD:SYMBOL:SIDE"` using **today's UTC date**, then
      read `$OUTPUT_DIR/execution_placed.jsonl` and check whether that
      `dedup_key` already appears for today. If it does, treat the intent as
      **ALREADY PLACED**: skip it, tell the operator plainly ("MSFT BUY was
      already placed today — mcp_order_id ‹…› — skipping to avoid a
      double-fill"), record a `skipped` receipt with a note, and move on. Do
      **not** re-place. (If the ledger file is absent, no intent has been
      placed today — proceed.) Also skip any intent with a still-pending
      approval for the same symbol and side (from the refresh in step 2 or a
      fresh `get_trade_approvals`): one request per order is waiting already.
   c. **Confirmation — pick the path from THIS order's review response** (see
      "Robinhood trade-approval mode" above):
      - **Approval path** (`customer_approval_required: true`, empty
        `order_checks`): tell the operator what you are sending to their phone
        and go to d. without asking in chat — the tap in the app is the
        confirmation.
      - **In-chat path** (everything else): show the final order and ask the
        operator to confirm THIS specific order ("place / skip / stop").
        Require an explicit affirmative per order — never batch-confirm, and
        never treat silence or a topic change as consent.
   d. **Submit with a deterministic `ref_id`.** Compute it from the intent's
      `client_order_id` (never a random UUID):
      ```bash
      python3 -c "from execution.receipts_store import make_ref_id; print(make_ref_id('<client_order_id>'))"
      ```
      If it prints `None` (blank `client_order_id`), do not place — skip the
      intent with a note. Call `place_equity_order` with that `ref_id` (and
      the resolved limit price for limit intents). On a transient transport
      failure, retry with the **same** `ref_id` — Robinhood deduplicates on
      it, so a retry can never become a second order. On "skip", move on. On
      "stop", end.
   e. **On a successful placement, append to BOTH ledgers (append-only):**
      - `$OUTPUT_DIR/execution_placed.jsonl` — the placed-intent ledger:
        `{"ts","dedup_key","symbol","side","qty","target_notional",
        "client_order_id","mcp_order_id","approval_id","ref_id"}` (use the same
        `dedup_key` you computed in step 5b; this is what makes the next run's
        idempotency check work). Write it through the helper, not by hand:
        `python3 -c "from execution.receipts_store import append_placed; append_placed({...})"`.
        On the approval path, `approval_id` is the id the
        `place_equity_order` response returns. If the response carries none,
        call `get_trade_approvals` and match on symbol, side and quantity; if
        you still cannot identify it, record `approval_id: null` and tell the
        operator — never invent one. Then record its first state:
        `append_approval_event({"approval_id", "state": "pending", "symbol",
        "side", "dedup_key", "ref_id"})`.
      - `$OUTPUT_DIR/execution_receipts.jsonl` — the outcome audit trail:
        `{"ts","symbol","side","qty","action":"reviewed|placed|skipped",
        "mcp_order_id","note"}`.
      For reviewed-only or skipped intents, append only the receipts record (no
      ledger line — nothing was placed).
6. **Track approvals.** For the approval ids submitted this run, call
   `get_trade_approvals(account_number, approval_ids=[...])` once before
   reporting. Record every state you observe — including `pending`, so the
   next run knows to re-check it:
   ```bash
   python3 -c "from execution.receipts_store import append_approval_event; append_approval_event({'approval_id': '<id>', 'state': '<state as returned>', 'symbol': '<SYM>', 'side': '<side>'})"
   ```
   Pass Robinhood's state string verbatim; the helper normalises it
   (pending / approved / declined / expired / auto_approved, else `unknown`
   with the raw value kept). Never state that something is or isn't pending
   without a fresh `get_trade_approvals` call — the operator approves and
   declines in the app, and approvals expire on their own.
   - **Declined or expired** is final for the day: the ledger's `dedup_key`
     already blocks a same-day resubmit, and you never resubmit one on your
     own — a decline is the operator's decision, not a transient error.
   - **Stale pending approval** (a later run's queue no longer has that
     intent as `allow_place: true`, or now holds the opposite side): tell the
     operator and offer to decline it with `decline_trade_approval`. Call it
     only after their explicit OK, then record the `declined` state.
7. **Report, and stay open.** Summarise what was previewed, placed, and
   skipped, point the operator to `$OUTPUT_DIR/execution_receipts.jsonl`,
   `$OUTPUT_DIR/execution_placed.jsonl`, and the Robinhood app, and invite any
   follow-up questions rather than treating the run as over the moment the last
   intent is handled. Note that after the run, `execution/receipts_store.py`
   reconciles the receipts/ledger against the account's **actual** Robinhood
   fills (via `get_equity_orders`), and the **Robinhood panel in the GUI Command
   Center surfaces that reconciliation** — direct the operator there to confirm
   every intent the ledger records as placed shows a matching real fill (and to
   catch any drift).

## Invariants (never violate)

- **Preview before place, always.** `review_equity_order` precedes any
  `place_equity_order` for the same intent.
- **Never place in `review` mode.** Never place an `allow_place: false` intent.
- **One explicit human confirmation per placed order.** No auto/batch placement.
  On the approval path that confirmation is the operator's tap in the Robinhood
  app — and only when THIS order's `review_equity_order` response shows
  `customer_approval_required: true` with empty `order_checks`. Otherwise ask
  in chat ("place / skip / stop") exactly as before.
- **You never approve a trade.** Only the operator can, in the app. Decline a
  pending approval only with the operator's explicit OK.
- **Deterministic ref_id.** Every `place_equity_order` carries
  `make_ref_id(client_order_id)`; a retry re-sends the same one.
- **Approval states come from a fresh call.** Never claim an approval is or
  isn't pending from memory; record every observed state through
  `append_approval_event`, and never resubmit a declined or expired order.
- **Honor the kill switch** at load time and again immediately before each
  placement.
- **Agentic account only.** Never act against the operator's main account.
- **Idempotent placement.** Before placing, check the placed-intent ledger
  (`$OUTPUT_DIR/execution_placed.jsonl`) for today's `dedup_key`; if present,
  the intent is already placed — skip it, never double-place. Append a ledger
  line after every successful placement so the next run sees it.
- **Preflight vetoes are final.** With a preflight for this queue, place only
  its `passed: true` intents at its `qty` and `limit_price`; without one, every
  order takes the in-chat path.
- **Limit price from the live quote.** For a `limit` intent, use the
  preflight's `limit_price` (computed from a live quote fetched for this
  queue); without a preflight, derive the limit price from the review-time MCP quote (BUY ≤ quote·(1+bps/10000), SELL ≥
  quote·(1−bps/10000)) — never from the queue's stale snapshot price — and pass
  the same price to both `review_equity_order` and `place_equity_order`.
- **Receipts, not intents.** You append outcomes to
  `$OUTPUT_DIR/execution_receipts.jsonl` (and placements to
  `$OUTPUT_DIR/execution_placed.jsonl`); you never edit
  `$OUTPUT_DIR/execution_queue.json` (the platform owns it).
- **Conversation, not consent.** Narrating, answering questions, and
  discussing an intent is encouraged and never itself counts as the operator's
  explicit per-order confirmation — that confirmation still has to be asked
  for and given plainly, per step 5c (on the approval path, the tap in the
  Robinhood app is that confirmation).
