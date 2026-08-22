#!/bin/bash
mkdir -p output

echo "Starting Auditor 1 (Safety)..."
cat /tmp/pr1_clean.diff /tmp/pr2_clean.diff | claude -p "You are the Safety & Validation Auditor. Audit these diffs for Stockpy. Focus on numeric bounds, leg prices, and validation gates in apply_multi_leg_fill and apply_fill. Watch out for KeyError if fill_price is missing. Return your findings." > output/audit_safety.txt &

echo "Starting Auditor 2 (Attribution)..."
cat /tmp/pr1_clean.diff /tmp/pr2_clean.diff | claude -p "You are the Attribution Auditor. Audit these diffs for Stockpy. Focus on strategy_id propagation in apply_multi_leg_fill/apply_fill/apply_roll_fill, missing strategy_id params, and DB migration idempotency in PaperAccountStore. Return your findings." > output/audit_attribution.txt &

echo "Starting Auditor 3 (Error Handling)..."
cat /tmp/pr1_clean.diff /tmp/pr2_clean.diff | claude -p "You are the Error Handling Auditor. Audit these diffs for Stockpy. Focus on fail-closed atomicity, transaction rollbacks, and rejected order states. Return your findings." > output/audit_error_handling.txt &

echo "Waiting for auditors to finish..."
wait
echo "Audit complete! Results saved to output/audit_safety.txt, output/audit_attribution.txt, and output/audit_error_handling.txt"
