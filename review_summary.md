# Stockpy Jules Queue — Review Findings & Actions Taken

Based on the queue review plan, I have completed the requested audits and implemented the safe changes directly into the repository. Below is the summary of findings and the status of each item.

## Track 1 — The N+1 / Performance PRs

**1. Analysis of `session_scope()`**
*   **Finding:** Reviewed `db_config.py`. `session_scope()` only calls `session.commit()` once at the end of the `yield` block. Therefore, SQLAlchemy defers all flush operations to the final commit regardless of whether `session.add()` is called in a loop or `session.add_all()` is used. The "N+1" label is technically inaccurate (no reduction in DB round-trips), but batching is a harmless syntactic optimization.

**2. `sector_correlation_store.py` and `cap_audit_store.py`**
*   **Action Taken:** I refactored both files to use `session.add_all()` to match Jules's intent. 
*   **Verification:** I added specific atomicity tests (`test_record_cap_events_mid_batch_failure_rolls_back_entire_batch` and the equivalent for sector correlation) to enforce CONSTRAINT #6. I verified that a forced mid-batch failure cleanly aborts the entire transaction without committing partial data.

**3. `broker_fills_store.py` (Upsert logic)**
*   **Action Taken:** I refactored `record_instrument_symbols` to correctly apply `add_all()` *only* to the new-row insert branch, leaving the update branch intact as per the review instructions.
*   **Verification:** I added `test_record_instrument_symbols_mid_batch_failure_rolls_back_entire_batch` which confirms that an error mid-processing degrades to an empty mapping without persisting partial state.

**4. Schema Migration PR (`fix-n-plus-1-schema-migration-1819301733788490750`)**
*   **Finding:** **Reject this PR.** The PR attempts to wrap the `ALTER TABLE` loop with an explicit `cursor.execute("BEGIN TRANSACTION;")`. Because `dbapi_conn` is acquired from an active SQLAlchemy `session` that has already started a transaction implicitly, this will trigger a `sqlite3.OperationalError: cannot start a transaction within a transaction`. SQLite DDL statements also have weaker transactional boundaries.

## Track 2 — Docs, Audits, and Recurring Tasks

**1. Agentic Trading Safety Framework (Docs)**
*   **Finding:** I reviewed the PR on branch `origin/agentic_safety_framework_docs:docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`. 
*   **Verification:**
    *   *Values Check:* The thresholds cited (e.g., `MAX_POSITION_WEIGHT=1.0`, `DAILY_LOSS_LIMIT_PCT=0.02`, `MAX_ORDER_RATE_PER_MIN=10`) are accurate and reflect `settings.py`.
    *   *Kill-switch Check:* `GlobalKillSwitch` is correctly referenced and actively evaluated by `PreTradeRiskGate`.
    *   *Prose Gate Check:* The document accurately captures that Robinhood execution and the Jules `confirm=True` gate are driven by prompt prose (`.claude/skills/robinhood-execution/SKILL.md` and `docs/JULES_INTEGRATION.md`), not strict code-level enforcement.
*   **Action Taken:** Keep this version of the document.

**2. Data-Leakage Auditor**
*   **Action Taken:** I created/updated `.github/workflows/ml_leakage_audit.yml` on `main`.
*   **Verification:** I widened the trigger globs to explicitly include `sizing/**` (meta-labeling) and `scripts/*forecast_backfill*`. I also added a step (`Output Watched Paths`) that echoes the watched paths to the CI log so coverage gaps are visible on every run.

**3. Recurring Weekly Tasks (Advice for Jules)**
*   **Dependency/CVE Check:** Fine to proceed. Ensure Jules scopes this strictly to `requirements.txt` CVEs to avoid stepping on the parallel CodeQL remediation work (which handles secrets/hardcoded credentials).
*   **Metrics Validator:** Recommend re-scoping this task. Instead of duplicating the execution of the `Gravity AI Review Suite`, the weekly task should be configured to *read the output* of the most recent CI run and independently spot-check the reported metrics against raw computation.
