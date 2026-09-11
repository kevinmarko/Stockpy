import pathlib

class TestExecutionBoundary:
    def test_daemon_loop_isolated_from_explore_surfaces(self):
        """Assert that the daemon's autonomous loop and core engines never import
        explore-only surfaces like fmp_screener, ensuring the execution universe
        is strictly bounded to the tracked universe."""
        offenders = []
        # Core autonomous pipeline files
        core_files = [
            "main_orchestrator.py",
            "main.py",
            "pipeline/production_steps.py",
            "engine/advisory.py",
            "execution/options_lifecycle.py",
        ]
        
        banned_substrings = [
            "fmp_screener",
        ]
        
        for f in core_files:
            path = pathlib.Path(f)
            if not path.exists():
                continue
            src = path.read_text(encoding="utf-8")
            for banned in banned_substrings:
                if banned in src:
                    offenders.append(f"{f} contains {banned}")
        
        assert not offenders, f"Core autonomous loops must not import explore-only modules: {offenders}"

