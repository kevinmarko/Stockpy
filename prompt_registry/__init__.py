"""
prompt_registry/
================
Remote-updatable, cryptographically-signed Prompt Registry for the InvestYo
advisory quant platform.

Provides a versioned store for every AI-facing instruction: the master
pre-prompt, per-stage development task prompts, and the runtime Gravity AI
auditor prompts (``ai_verification_prompts.py``).

**Hard security boundary (must never regress)**
    Prompts are *instructional / narrative* text only.  The registry can change
    what an AI is *told* — it cannot change what the platform is *permitted to
    do*.  Order submission, the advisory quarantine, the risk gate, and the kill
    switch are enforced in Python and are out of the registry's reach.  A
    fetched prompt that references a safety-control bypass is rejected by
    :mod:`prompt_registry.guardrails` and the last known-good version (or the
    committed baseline) is used instead.

Stages shipped so far
---------------------
Stage 1:  ``models``, ``signing``, ``guardrails`` — pure, headless foundation.
Stage 2:  ``store``, ``cache``, ``baseline/`` — storage backends, disk cache,
          committed fail-closed prompt defaults.
Stage 3 (current):
          ``registry`` — resolution orchestration (pin → remote → cache →
          baseline).  ``get_registry()`` singleton; ``PromptRegistry.get()``,
          ``sync()``, ``rollback()``.
Stage 4:  ``__main__`` — CLI (list / get / sync / pin / rollback / diff /
          publish / verify).
Stage 5:  Wire ``ai_verification_prompts.py`` to source from registry.
Stage 6+: Settings, GUI tab, docs/Gravity step 69.

Resolution order (once Stage 3 is complete)
-------------------------------------------
1. **Pin** — explicit version from ``settings.PROMPT_REGISTRY_PINS``.
2. **Remote ``latest``** — only if fetched AND signature-valid AND
   guardrail-clean.
3. **Disk cache** — last known-good signed version.
4. **Baseline** — committed ``prompt_registry/baseline/*.md``  (always
   present; a prompt is *never* empty — CONSTRAINT #4).

Usage
-----
    from prompt_registry import get_registry, PromptRegistry, PromptRecord

    # Resolution-chain lookup (pin → remote → cache → baseline → default):
    body = get_registry().get("gravity.system", default=BASELINE_SYSTEM)

    # Fetch remote manifest and pre-warm the disk cache:
    get_registry().sync()

    # Roll back to the previous cached version in-memory:
    get_registry().rollback("master_preprompt")
"""

from __future__ import annotations

# Stage 1 public surface
from prompt_registry.models import PromptRecord, PromptVersion, RegistryManifest
from prompt_registry.signing import sign, verify, compute_sha256
from prompt_registry.guardrails import validate_prompt

# Stage 2 public surface
from prompt_registry.store import (
    PromptStore,
    LocalJSONStore,
    HTTPStore,
    FirestoreStore,
    RegistryFetchError,
    ReadOnlyStoreError,
)
from prompt_registry.cache import (
    CacheManager,
    read_baseline,
    list_baseline_ids,
)

# Stage 3 public surface
from prompt_registry.registry import (
    PromptRegistry,
    get_registry,
    reset_registry,
)

__all__ = [
    # models
    "PromptRecord",
    "PromptVersion",
    "RegistryManifest",
    # signing
    "sign",
    "verify",
    "compute_sha256",
    # guardrails
    "validate_prompt",
    # stores (Stage 2)
    "PromptStore",
    "LocalJSONStore",
    "HTTPStore",
    "FirestoreStore",
    "RegistryFetchError",
    "ReadOnlyStoreError",
    # cache + baseline (Stage 2)
    "CacheManager",
    "read_baseline",
    "list_baseline_ids",
    # registry (Stage 3)
    "PromptRegistry",
    "get_registry",
    "reset_registry",
]
