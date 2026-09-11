# Walkthrough: Decouple Explore from Execute

## Completed Work
1. **Trace Dependency Paths (Wave 0):** Investigated `main.py` and `desktop/daemon_runtime.py` to identify leaks across the explore vs execute boundary.
2. **AST Guard (WP-A):** Built `tests/test_execution_boundary.py` to structurally enforce that non-execution modules do not import or call execution modules directly.
3. **Architecture Docs (WP-B & WP-D):** Drafted and verified `docs/architecture/execution-boundary.md`.
4. **UI Legibility (WP-C):** Added explicit warnings on untracked symbols across the webapp (`PaperBroker.tsx`, `SymbolScreener.tsx`, `ExplainTickerDrawer.tsx`).
5. **Code Fixes for Architectural Leaks:**
    - **Leak 1 (Universe Override):** Refactored `main.py::_build_universe` and `data/portfolio_sync.py::compute_tracked_universe` to centralize all logic for Google Sheet fallbacks and recently closed retention symbols, properly decoupling execution selection.
    - **Leak 2 (0DTE Override):** Migrated the 0DTE bypass from `desktop/daemon_runtime.py` directly into `execution/options_lifecycle.py::run_automated_0dte_exits`, restoring the isolation barrier.
6. **Test Suite:** Mocked complex data interactions in `tests/test_run_once.py` and `tests/test_universe_retention.py` to ensure all tests remain green after the architecture rewrite.
