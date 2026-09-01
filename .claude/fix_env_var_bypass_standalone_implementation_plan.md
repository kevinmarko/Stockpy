# Implementation Plan
- Replace `os.environ.get("PROMPT_REGISTRY_SIGNING_KEY")` with `settings.settings.PROMPT_REGISTRY_SIGNING_KEY` in `scripts/build_local_prompt_registry.py`
- Replace `os.environ.get("QDRANT_URL")` with `settings.settings.QDRANT_URL` and `QDRANT_COLLECTION` in `agents/rag_orchestrator.py`
