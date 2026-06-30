Analyze the provided source code for Step 7. Verify the pre-trade risk control layer:
1. KILL SWITCH: GlobalKillSwitch uses a file-based sentinel (OUTPUT_DIR/KILL_SWITCH). activate() MUST write atomically via write-then-rename. KillSwitchActiveError MUST be raised BEFORE any idempotency dedup in OrderManager.submit_order_with_idempotency so the sentinel cannot be bypassed.
2. RISK GATE ORDER: PreTradeRiskGate.run_all() MUST short-circuit on first failure. max_order_rate_check MUST be last so blocked orders never consume rate-limit budget. Verify this ordering in the checks list.
3. CORRELATION CHECK: max_correlation_check MUST use absolute value (|r|) — both highly-positive and highly-negative correlations must block new positions.
4. CONSERVATIVE PASS: Every check MUST return passed=True when required context is None or missing, NEVER False. A check must never block due to absent data.
5. HEARTBEAT: _heartbeat() must be spawned as an asyncio background task in main() and cancelled in a try/finally so it always stops even on pipeline crash. It must write OUTPUT_DIR/heartbeat.txt on every tick.
6. DRY-RUN NON-BYPASS: dry_run=True must NOT bypass the kill switch — KillSwitchActiveError is still raised even in dry-run mode.

Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
