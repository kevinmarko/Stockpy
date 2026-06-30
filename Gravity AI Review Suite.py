"""
Gravity AI Review Suite — launcher (Phase 4b refactor, 2026-06-29)
=====================================================================
Thin entry point that delegates to the ``gravity`` package.  The class and all
audit steps now live in ``gravity/__init__.py`` so they are importable and
testable without running this script.

Usage (unchanged):
    python3 "Gravity AI Review Suite.py"
"""
from gravity import GravityAIAuditor

# =============================================================================
# EXECUTION (GRAVITY AI ENTRY POINT)
# =============================================================================
if __name__ == "__main__":
    print("Initializing Gravity AI Verification Suite...\n")
    auditor = GravityAIAuditor()
    json_output = auditor.export_machine_readable_report()

    # Output the structured, machine-readable format for the AI to parse
    print(json_output)
    print("\n✅ Verification Suite Complete. Ready for Gravity AI ingestion.")
