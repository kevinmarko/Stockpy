# Agentic Trading Safety Framework - Walkthrough

## What was done
1. **New Document**: Wrote `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` breaking down Stockpy's agentic capabilities and deterministic guardrails.
2. **Fact Check**: Fired off 5 subagents to verify every claim. Verified that `MAX_PORTFOLIO_HEAT` (0.06), `MAX_POSITION_WEIGHT` (1.0), and other limits match exactly what is in `settings.py`.
3. **Index Updated**: Appended the new document to `docs/README.md` and `CLAUDE.md`.
4. **Correction Made**: A subagent found that `api/auth.py` handles internal tokens, not the Robinhood device-approval login. I corrected the document to accurately cite `api/_rh_login.py` and `data/robinhood_login.py`.

The changes have been pushed and are ready for review. No runtime code was touched.
