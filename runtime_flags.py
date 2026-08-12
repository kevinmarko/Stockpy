"""Runtime settings store — READ PATH ONLY.

Lets a ``Settings`` field value be sourced from a JSON file on disk
(``output/runtime_flags.json``) in ADDITION to the two layers that already
exist (real shell environment variables, and ``.env`` via pydantic-settings'
own ``env_file=``). This is the half that *reads and applies* the store. The
WRITER — an API endpoint that creates/updates the file, plus its audit log —
is deliberately NOT part of this module and does not exist yet.

**With no store file present, this module is a no-op.** That is the property
that makes it safe to ship before the writer exists: nothing about this
platform's configuration changes for anyone until a store file actually
appears on disk. ``tests/test_runtime_flags.py::TestByteIdenticalWithNoFile``
proves it by diffing a full 320-field ``model_dump()`` with and without the
apply step.

--------------------------------------------------------------------------
The JSON shape (contract for the not-yet-built writer)
--------------------------------------------------------------------------
::

    {
      "version": 1,
      "flags": {
        "FORECAST_PROPHET_WEIGHT": {
          "value": 0.35,
          "updated_at": "2026-08-03T12:00:00+00:00",
          "updated_by": "pilots_api"
        },
        "BETA_LOOKBACK_DAYS": {"value": 300}
      }
    }

Every entry is an OBJECT with a required ``"value"`` key, never a bare scalar.
This module reads ``"value"`` and ignores every sibling key, so the writer is
free to add audit metadata (``updated_at``, ``updated_by``, a reason string,
a prior value) without a schema change here.

The envelope is required rather than merely allowed because the bare-scalar
form is genuinely ambiguous for this settings model: ``SECTOR_FORECAST_CONFIGS``
is a ``dict``-typed field, so a permissive reader could not tell an envelope
from a dict-typed field's real value that happened to carry a ``"value"`` key.
One unambiguous shape is worth more than the convenience of hand-writing a
scalar. A bare-scalar entry is skipped as a per-key dead letter with a WARNING
naming the field and the expected shape.

--------------------------------------------------------------------------
Precedence: real shell env > store > .env > field default
--------------------------------------------------------------------------
An operator who runs ``SOME_FIELD=x python3 main.py`` must never have a stale
JSON file silently override that explicit, deliberate choice. So any field
whose name is set in the REAL shell environment is skipped by the apply step
and reported as env-pinned.

"Real shell environment" is not the same as ``os.environ`` membership.
pydantic-settings' own ``env_file=`` loading does NOT mutate ``os.environ``
(verified empirically against pydantic-settings 2.14.2), but ~14 call sites in
this codebase ALSO call python-dotenv's ``load_dotenv()`` — for the smaller set
of raw ``os.environ.get(...)`` readers elsewhere — and that DOES copy ``.env``
into ``os.environ``. So membership alone would misclassify every ``.env`` line
as a shell export in any process that ran the loader.

:func:`real_environment_keys` subtracts the ``.env``-attributable names back
out. A name in ``os.environ`` is treated as a real shell export UNLESS ``.env``
declares that same name AND ``.env``'s parsed value is byte-identical to what
is live in ``os.environ``. That second clause is what makes the subtraction
sound rather than merely convenient: every ``load_dotenv()`` call site in this
repo passes ``override=False`` (audited — see the PR body), so the loader can
only ever ADD a name that was absent, never change one that was already there.
A differing value therefore PROVES a real shell export won, and pinning it is
correct. The residual ambiguity — name in both, values equal — is decided in
favour of letting the store apply, and in that case the two candidate values
are identical anyway, so the only thing at stake is whether a subsequent store
edit is allowed to move it.

``.env`` is parsed with ``dotenv_values()``, which reads without mutating
``os.environ`` — the same read-only idiom ``gui/env_io.py::_raw_env`` already
uses. Comparison is case-insensitive because ``Settings.model_config`` sets
``case_sensitive=False``.

--------------------------------------------------------------------------
Why ``setattr``-after-construction, not a pydantic settings source
--------------------------------------------------------------------------
A ``settings_customise_sources`` hook can only affect the value at the moment
``Settings()`` is constructed. This repo constructs its singleton exactly once,
at ``settings.py`` import time, and 146+ modules do ``from settings import
settings`` — binding the OBJECT, not a name that could later be reassigned.
There is therefore no supported way to reconstruct the singleton later without
leaving every one of those modules holding the stale original. Layering onto
the already-constructed object is what makes a live refresh possible at all
(that refresh is a later task; this module only performs the one apply at
import).

Assignment goes through ``Settings.__pydantic_validator__.validate_assignment``,
NOT ``pydantic.TypeAdapter``. ``TypeAdapter`` coerces to the field's annotated
type but silently BYPASSES ``@field_validator`` decorators — verified: it maps
``ROBINHOOD_EXECUTION_MODE="garbage-value"`` straight through, where the real
validator collapses anything outside ``{off, review, live}`` to the inert
``off`` precisely so a bad value can never arm live execution.
``validate_assignment`` runs the validator (``"  LIVE  "`` -> ``"live"``),
coerces ordinary types (``"300"`` -> ``300``, ``"false"`` -> ``False``), writes
the coerced value into ``settings.__dict__`` in place, and raises
``ValidationError`` without mutating anything on a bad value. This is the same
mechanism ``api/pilots_api.py``'s ``PUT /llm/setting`` uses.

--------------------------------------------------------------------------
Module constraints
--------------------------------------------------------------------------
* **stdlib-only leaf, imported BY ``settings.py``.** It must never import
  ``settings`` (or anything that does) — that would be a circular import and
  ``import settings`` would fail for the entire application. The one project
  import allowed is ``settings_keysets``, which was built for exactly this and
  imports nothing but ``__future__``. ``python-dotenv`` is imported lazily
  inside the one function that needs it, so a broken/missing dotenv install
  degrades this module rather than breaking ``import settings``. Enforced by
  ``tests/test_runtime_flags.py::TestModuleIsADependencyFreeLeaf``.
* **Path anchoring** (CLAUDE.md / ``settings.py``'s ``ENV_PATH`` comment): the
  store path is anchored to THIS FILE's own location via
  ``Path(__file__).resolve().parent``, never ``find_dotenv()``-style upward
  walking and never the process CWD. A real bug in this repo had
  ``find_dotenv()`` walk up out of a git worktree into a PARENT checkout's
  ``.env``; the same failure mode would have this module read a sibling
  checkout's operator state. It re-derives the repo root independently rather
  than importing ``settings.OUTPUT_DIR``, because ``OUTPUT_DIR`` is itself a
  ``Settings`` field (and a ``BOOTSTRAP_KEYS`` member for exactly this
  self-referential reason).
* **Never raises** (CONSTRAINT #6). Missing file, corrupt JSON, wrong schema,
  unknown field, bad value — every one degrades to "apply less" with a WARNING,
  never to an exception. One bad entry never poisons the rest of the file
  (dead-letter-per-key, the same shape ``HistoricalStore`` uses).
* **Never fabricates** (CONSTRAINT #4). A skipped key leaves the field at
  whatever ``.env``/env/default already produced; nothing here invents a
  substitute value.
* **Never logs a stored VALUE.** A store file may hold a credential. Log
  messages carry field NAMES and pydantic's ``errors()[0]["msg"]`` only —
  never ``str(exc)``, which embeds ``input_value=`` and would leak the
  rejected value into the log.

Known gap, deliberately left to the writer task: this module does not refuse
``gui/env_io.py::SECRET_KEYS`` fields. It cannot import that set (``gui.env_io``
imports ``settings``), and duplicating an 80-key credential list into a leaf
module is a drift hazard worse than the gap. Refusing to WRITE a secret into
the store belongs in the writer, which has no such import restriction.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from settings_keysets import BOOTSTRAP_KEYS

logger = logging.getLogger(__name__)

__all__ = [
    "SCHEMA_VERSION",
    "STORE_FILENAME",
    "PATH_OVERRIDE_ENV_VAR",
    "DEFAULT_STORE_PATH",
    "ApplyReport",
    "store_path",
    "load_store",
    "real_environment_keys",
    "apply_overrides",
]

SCHEMA_VERSION = 1

#: Filename under ``<repo root>/output/``. Git-ignored (``/output/`` is
#: blanket-ignored) — this is per-machine operator state, never committed.
STORE_FILENAME = "runtime_flags.json"

#: Escape hatch for tests and for an operator relocating the store. Read at
#: call time (not import time) so a test can set it after this module is
#: already imported, including in a subprocess.
PATH_OVERRIDE_ENV_VAR = "INVESTYO_RUNTIME_FLAGS_PATH"

#: Deliberate, documented duplication of ``settings.LOCAL_DATA_ROOT``'s own
#: default literal (``Path.home() / ".stockpy_local"``). This module is a
#: dependency-free stdlib-only leaf that must NEVER import ``settings`` (see
#: the module docstring), so it cannot read ``settings.LOCAL_DATA_ROOT``
#: directly -- exactly the same reason this module already re-derives the
#: repo root independently rather than importing ``settings.OUTPUT_DIR``.
#: ``tests/test_runtime_flags.py`` pins the two literals against each other
#: so they cannot silently drift apart.
#: Never CWD-relative, never an upward search. See the module docstring.
DEFAULT_STORE_PATH = Path.home() / ".stockpy_local" / "output" / STORE_FILENAME


@dataclass(frozen=True)
class ApplyReport:
    """Outcome of one :func:`apply_overrides` pass.

    Purely descriptive — a later task surfaces this to an operator so they can
    see why a store edit did or did not take effect. Nothing reads it to make a
    decision today.
    """

    #: Absolute path consulted.
    path: str
    #: Whether a file existed at ``path`` at all.
    store_present: bool = False
    #: field name -> the COERCED value now live on the settings object (read
    #: back after validation, never the raw JSON input).
    applied: dict[str, Any] = field(default_factory=dict)
    #: Skipped: in ``settings_keysets.BOOTSTRAP_KEYS``.
    skipped_bootstrap: tuple[str, ...] = ()
    #: Skipped: the name is set in the real shell environment, which wins.
    skipped_env_pinned: tuple[str, ...] = ()
    #: Skipped: not a real ``Settings.model_fields`` name.
    skipped_unknown: tuple[str, ...] = ()
    #: Skipped: failed validation / wrong entry shape. field -> reason. Reasons
    #: never embed the rejected value (it may be a credential).
    skipped_invalid: dict[str, str] = field(default_factory=dict)
    #: File-level failure (unreadable, not JSON, wrong envelope). When set, no
    #: key was applied. ``None`` on the healthy path INCLUDING "no file".
    error: Optional[str] = None

    @property
    def any_applied(self) -> bool:
        return bool(self.applied)


def store_path(path: Optional[Any] = None) -> Path:
    """Resolve the store's location.

    Precedence: explicit ``path`` argument > ``INVESTYO_RUNTIME_FLAGS_PATH``
    environment variable > :data:`DEFAULT_STORE_PATH`.
    """
    if path is not None:
        return Path(path)
    override = os.environ.get(PATH_OVERRIDE_ENV_VAR)
    if override:
        return Path(override)
    return DEFAULT_STORE_PATH


def _dotenv_entries() -> dict[str, Optional[str]]:
    """Parse the repo-root ``.env`` WITHOUT mutating ``os.environ``.

    Anchored to this file's own directory, matching ``settings.ENV_PATH``
    exactly (``tests/test_runtime_flags.py`` asserts the two agree). Returns
    ``{}`` on any failure — a missing ``.env`` is the normal case in CI and in
    a fresh worktree.
    """
    env_path = Path(__file__).resolve().parent / ".env"
    try:
        if not env_path.exists():
            return {}
        from dotenv import dotenv_values  # lazy: never break `import settings`

        return dict(dotenv_values(env_path))
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "runtime_flags: could not parse %s to separate .env keys from real "
            "shell exports; treating every os.environ name as a real export "
            "(the conservative direction — more fields stay env-pinned).",
            env_path,
        )
        # Sentinel: an empty mapping makes every os.environ name look like a
        # real export, i.e. MORE pinning, i.e. the store applies less. Failing
        # in the direction that cannot override an operator's explicit choice.
        return {}


def real_environment_keys() -> frozenset[str]:
    """Upper-cased names that are set in the REAL shell environment.

    A name in ``os.environ`` counts UNLESS ``.env`` declares that same name and
    ``.env``'s parsed value equals what is live in ``os.environ`` — in which
    case the value is ``.env``-attributable (some entry point ran
    ``load_dotenv(..., override=False)``) and the store is allowed to move it.
    See the module docstring for why ``override=False`` at every call site is
    what makes this sound.
    """
    dotenv_pairs = {
        str(k).upper(): v for k, v in _dotenv_entries().items() if k is not None
    }
    pinned: set[str] = set()
    for name, live_value in os.environ.items():
        upper = name.upper()
        if upper in dotenv_pairs and dotenv_pairs[upper] == live_value:
            continue  # attributable to .env, not a shell export
        pinned.add(upper)
    return frozenset(pinned)


def load_store(path: Optional[Any] = None) -> tuple[dict[str, Any], Optional[str]]:
    """Read and shape-check the store file.

    Returns ``(flags, error)``. ``flags`` maps field name -> raw stored value
    (already unwrapped from its ``{"value": ...}`` envelope). ``error`` is a
    human-readable string on a FILE-level problem, else ``None``.

    A missing file is ``({}, None)`` — absence is not an error, it is the
    default state of every install.
    """
    resolved = store_path(path)

    # Fast path: one stat, no try/except around an open. This runs at
    # `import settings` time in every process, and no-file is the common case.
    if not resolved.exists():
        return {}, None

    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        # Corrupt/unreadable file. Never let this stop `import settings`.
        return {}, f"could not read/parse {resolved}: {type(exc).__name__}"

    if not isinstance(raw, Mapping):
        return {}, f"{resolved}: top level must be a JSON object, got {type(raw).__name__}"

    version = raw.get("version")
    if version != SCHEMA_VERSION:
        # Refuse rather than guess. A future writer bumping the version means
        # the shape changed; applying it under today's assumptions is exactly
        # the fabrication CONSTRAINT #4 forbids.
        return {}, (
            f"{resolved}: unsupported schema version {version!r} "
            f"(this build reads version {SCHEMA_VERSION})"
        )

    flags = raw.get("flags")
    if not isinstance(flags, Mapping):
        return {}, f"{resolved}: 'flags' must be a JSON object, got {type(flags).__name__}"

    unwrapped: dict[str, Any] = {}
    for key, entry in flags.items():
        if not isinstance(key, str):
            continue
        if isinstance(entry, Mapping) and "value" in entry:
            unwrapped[key] = entry["value"]
        else:
            # Per-key dead letter, not a file-level failure: every other entry
            # in this file is still applied.
            logger.warning(
                "runtime_flags: ignoring %r — each entry must be an object with "
                "a 'value' key, e.g. {\"value\": 42}. Got %s.",
                key,
                type(entry).__name__,
            )
    return unwrapped, None


def apply_overrides(
    settings_obj: Any,
    *,
    path: Optional[Any] = None,
) -> ApplyReport:
    """Layer stored overrides onto an ALREADY-CONSTRUCTED settings object.

    Called once from the bottom of ``settings.py``, after the module-level
    ``settings = Settings()`` singleton exists. Mutates ``settings_obj`` in
    place via pydantic's validated-assignment machinery.

    Never raises. Every failure mode degrades to applying fewer keys.
    """
    resolved = store_path(path)
    flags, error = load_store(resolved)

    if error is not None:
        logger.warning("runtime_flags: %s — applying no overrides.", error)
        return ApplyReport(path=str(resolved), store_present=True, error=error)

    if not flags:
        # Covers both "no file" and "file with an empty flags object". Silent
        # by design: this is the state of every install until a writer exists,
        # and a log line on every `import settings` would be pure noise.
        return ApplyReport(path=str(resolved), store_present=resolved.exists())

    model_cls = type(settings_obj)
    try:
        model_fields = set(model_cls.model_fields)
        validator = model_cls.__pydantic_validator__
    except Exception as exc:  # pragma: no cover - defensive
        reason = f"settings object exposes no pydantic validator: {type(exc).__name__}"
        logger.warning("runtime_flags: %s — applying no overrides.", reason)
        return ApplyReport(path=str(resolved), store_present=True, error=reason)

    env_pinned = real_environment_keys()

    applied: dict[str, Any] = {}
    skipped_bootstrap: list[str] = []
    skipped_env: list[str] = []
    skipped_unknown: list[str] = []
    skipped_invalid: dict[str, str] = {}

    for key, value in flags.items():
        # 1. Bootstrap exclusion — absolute, checked before anything else.
        if key in BOOTSTRAP_KEYS:
            skipped_bootstrap.append(key)
            continue

        # 2. Must be a real field. `extra="ignore"` on this model means a bogus
        #    name would otherwise be silently dropped with a confusing error.
        if key not in model_fields:
            skipped_unknown.append(key)
            continue

        # 3. A real shell export always wins over the store.
        if key.upper() in env_pinned:
            skipped_env.append(key)
            continue

        # 4. Validated assignment — runs @field_validator, coerces, and leaves
        #    the field untouched if it raises.
        try:
            validator.validate_assignment(settings_obj, key, value)
        except Exception as exc:
            # NEVER str(exc): pydantic embeds `input_value=` in it, and a
            # stored value may be a credential. errors()[0]["msg"] does not.
            reason = _safe_validation_reason(exc)
            skipped_invalid[key] = reason
            continue

        # Read back the COERCED value that is actually live, never the raw
        # JSON input — "300" is stored, 300 is what took effect.
        applied[key] = getattr(settings_obj, key, None)

    if skipped_bootstrap:
        logger.warning(
            "runtime_flags: ignoring bootstrap-only key(s) %s from %s — these can "
            "never be sourced from the runtime store (see "
            "settings_keysets.BOOTSTRAP_KEY_REASONS); set them in .env instead.",
            sorted(skipped_bootstrap),
            resolved,
        )
    if skipped_unknown:
        logger.warning(
            "runtime_flags: ignoring unknown key(s) %s from %s — not a "
            "Settings field name.",
            sorted(skipped_unknown),
            resolved,
        )
    if skipped_env:
        logger.warning(
            "runtime_flags: %s set in the real environment; the shell value wins "
            "and the stored override was NOT applied.",
            sorted(skipped_env),
        )
    for key, reason in sorted(skipped_invalid.items()):
        logger.warning(
            "runtime_flags: ignoring %s from %s — %s", key, resolved, reason
        )
    if applied:
        # Names only. Values may be credentials.
        logger.info(
            "runtime_flags: applied %d override(s) from %s: %s",
            len(applied),
            resolved,
            sorted(applied),
        )

    return ApplyReport(
        path=str(resolved),
        store_present=True,
        applied=applied,
        skipped_bootstrap=tuple(sorted(skipped_bootstrap)),
        skipped_env_pinned=tuple(sorted(skipped_env)),
        skipped_unknown=tuple(sorted(skipped_unknown)),
        skipped_invalid=skipped_invalid,
    )


def _safe_validation_reason(exc: Exception) -> str:
    """A log-safe one-line reason for a rejected value.

    Uses pydantic's structured ``errors()[0]["msg"]``, which describes the
    CONSTRAINT ("Input should be a valid integer") without echoing the input.
    ``str(exc)`` is deliberately never used — it embeds ``input_value=``, which
    would print a rejected credential straight into the log.
    """
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            first = errors()[0]
            msg = first.get("msg")
            if msg:
                return str(msg)
        except Exception:  # pragma: no cover - defensive
            pass
    return f"rejected by validation ({type(exc).__name__})"
