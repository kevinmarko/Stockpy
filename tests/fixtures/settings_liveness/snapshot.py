"""Whole-run failure: a snapshot of the settings object.

model_dump() detaches every field at once, so no per-key answer downstream of
it is trustworthy. analyze() must raise UnresolvedAnalysis rather than emit a
partial partition.
"""
from settings import settings

SNAPSHOT = settings.model_dump()
