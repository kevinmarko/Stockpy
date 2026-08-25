"""Tests for data/sector_embeddings.py -- the semantic Related Sector
Selection feature's SBERT embedding + cosine similarity layer.

sentence-transformers is NOT installed in this environment (it's an
optional dependency, requirements-optional.txt) -- the absent-dependency
degradation path (SBERT_AVAILABLE is False here) is exercised directly;
the model-construction/pooling/caching logic is exercised by mocking
SBERT_AVAILABLE and _get_sbert_model, since those don't require the real
library to be importable."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import data.sector_embeddings as sector_embeddings
from data.sector_embeddings import (
    cosine_similarity,
    embed_text,
    load_sector_descriptions,
    resolve_target_description,
)


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = np.array([1.0, 2.0, 3.0])
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert cosine_similarity(np.array([1.0, 0.0]), np.array([-1.0, 0.0])) == pytest.approx(-1.0)

    def test_none_first_argument_is_nan(self):
        assert math.isnan(cosine_similarity(None, np.array([1.0, 0.0])))

    def test_none_second_argument_is_nan(self):
        assert math.isnan(cosine_similarity(np.array([1.0, 0.0]), None))

    def test_zero_vector_is_nan_not_zero(self):
        """CONSTRAINT #4: a degenerate zero-norm vector must not silently
        produce a fabricated 0.0 similarity."""
        assert math.isnan(cosine_similarity(np.array([0.0, 0.0]), np.array([1.0, 0.0])))

    def test_scale_invariant(self):
        v1 = np.array([1.0, 2.0, 3.0])
        v2 = np.array([2.0, 4.0, 6.0])  # same direction, different magnitude
        assert cosine_similarity(v1, v2) == pytest.approx(1.0)


class TestEmbedTextAbsentDependency:
    def test_sbert_unavailable_returns_none(self):
        """Real behavior in this environment -- sentence-transformers is
        not installed, so SBERT_AVAILABLE is genuinely False here."""
        assert sector_embeddings.SBERT_AVAILABLE is False
        assert embed_text("Some sector description") is None

    def test_empty_text_returns_none(self):
        assert embed_text("") is None
        assert embed_text(None) is None


class TestEmbedTextMockedAvailability:
    """Exercises the caching/pooling logic without requiring the real
    sentence-transformers library to be importable."""

    def test_calls_model_and_returns_vector(self, tmp_path):
        fake_model = MagicMock()
        fake_model.encode.return_value = [0.1, 0.2, 0.3]
        cache_path = tmp_path / "cache.json"

        with patch.object(sector_embeddings, "SBERT_AVAILABLE", True), \
             patch.object(sector_embeddings, "_EMBEDDING_CACHE_PATH", cache_path), \
             patch.object(sector_embeddings, "_get_sbert_model", return_value=fake_model):
            vector = embed_text("Technology sector", model_name="fake-model", pooling="max")

        assert vector is not None
        np.testing.assert_allclose(vector, [0.1, 0.2, 0.3])
        fake_model.encode.assert_called_once()

    def test_cache_hit_avoids_second_model_call(self, tmp_path):
        fake_model = MagicMock()
        fake_model.encode.return_value = [0.5, 0.5]
        cache_path = tmp_path / "cache.json"

        with patch.object(sector_embeddings, "SBERT_AVAILABLE", True), \
             patch.object(sector_embeddings, "_EMBEDDING_CACHE_PATH", cache_path), \
             patch.object(sector_embeddings, "_get_sbert_model", return_value=fake_model) as get_model:
            first = embed_text("Technology sector", model_name="fake-model", pooling="max")
            second = embed_text("Technology sector", model_name="fake-model", pooling="max")

        np.testing.assert_allclose(first, second)
        get_model.assert_called_once()  # second call served from the content-hash cache

    def test_different_text_is_a_cache_miss(self, tmp_path):
        fake_model = MagicMock()
        fake_model.encode.side_effect = [[1.0, 0.0], [0.0, 1.0]]
        cache_path = tmp_path / "cache.json"

        with patch.object(sector_embeddings, "SBERT_AVAILABLE", True), \
             patch.object(sector_embeddings, "_EMBEDDING_CACHE_PATH", cache_path), \
             patch.object(sector_embeddings, "_get_sbert_model", return_value=fake_model):
            v1 = embed_text("Technology sector", model_name="fake-model", pooling="max")
            v2 = embed_text("Healthcare sector", model_name="fake-model", pooling="max")

        assert not np.allclose(v1, v2)
        assert fake_model.encode.call_count == 2

    def test_different_pooling_is_a_cache_miss(self, tmp_path):
        """Same text, different pooling mode, must not share a cache entry
        -- otherwise the max-vs-mean pooling caveat would be silently
        masked by a stale cached vector."""
        fake_model = MagicMock()
        fake_model.encode.side_effect = [[1.0, 0.0], [0.0, 1.0]]
        cache_path = tmp_path / "cache.json"

        with patch.object(sector_embeddings, "SBERT_AVAILABLE", True), \
             patch.object(sector_embeddings, "_EMBEDDING_CACHE_PATH", cache_path), \
             patch.object(sector_embeddings, "_get_sbert_model", return_value=fake_model):
            embed_text("Technology sector", model_name="fake-model", pooling="max")
            embed_text("Technology sector", model_name="fake-model", pooling="mean")

        assert fake_model.encode.call_count == 2

    def test_model_exception_degrades_to_none(self, tmp_path):
        """CONSTRAINT #6: an embedding failure must never raise."""
        cache_path = tmp_path / "cache.json"
        with patch.object(sector_embeddings, "SBERT_AVAILABLE", True), \
             patch.object(sector_embeddings, "_EMBEDDING_CACHE_PATH", cache_path), \
             patch.object(sector_embeddings, "_get_sbert_model", side_effect=RuntimeError("boom")):
            assert embed_text("Technology sector") is None

    def test_cache_persisted_to_disk(self, tmp_path):
        fake_model = MagicMock()
        fake_model.encode.return_value = [0.7, 0.1]
        cache_path = tmp_path / "cache.json"

        with patch.object(sector_embeddings, "SBERT_AVAILABLE", True), \
             patch.object(sector_embeddings, "_EMBEDDING_CACHE_PATH", cache_path), \
             patch.object(sector_embeddings, "_get_sbert_model", return_value=fake_model):
            embed_text("Technology sector", model_name="fake-model", pooling="max")

        assert cache_path.exists()
        with open(cache_path) as f:
            cache = json.load(f)
        assert len(cache) == 1


class TestLoadSectorDescriptions:
    def test_loads_real_committed_yaml(self):
        descriptions = load_sector_descriptions()
        assert "Technology" in descriptions
        assert isinstance(descriptions["Technology"], str)


class TestResolveTargetDescription:
    def _yaml(self, tmp_path, targets=None):
        path = tmp_path / "descriptions.yaml"
        content = {"sectors": {"Technology": "desc"}, "targets": targets or {}}
        import yaml
        path.write_text(yaml.safe_dump(content))
        return path

    def test_override_takes_priority(self, tmp_path):
        path = self._yaml(tmp_path, targets={"NIO": "Chinese EV manufacturer."})
        store = MagicMock()
        result = resolve_target_description("NIO", historical_store=store, descriptions_path=path)
        assert result == "Chinese EV manufacturer."
        store.get_fundamentals_history.assert_not_called()

    def test_falls_back_to_fundamentals_raw_json(self, tmp_path):
        path = self._yaml(tmp_path, targets={})
        store = MagicMock()
        import pandas as pd
        store.get_fundamentals_history.return_value = pd.DataFrame([
            {"raw_json": json.dumps({"longBusinessSummary": "Makes electric vehicles."})}
        ])
        result = resolve_target_description("NIO", historical_store=store, descriptions_path=path)
        assert result == "Makes electric vehicles."

    def test_no_override_and_empty_fundamentals_returns_none(self, tmp_path):
        path = self._yaml(tmp_path, targets={})
        store = MagicMock()
        import pandas as pd
        store.get_fundamentals_history.return_value = pd.DataFrame()
        result = resolve_target_description("NIO", historical_store=store, descriptions_path=path)
        assert result is None

    def test_never_synthesizes_from_ticker_and_sector(self, tmp_path):
        """CONSTRAINT #4: no fabricated description from ticker+sector name."""
        path = self._yaml(tmp_path, targets={})
        store = MagicMock()
        import pandas as pd
        store.get_fundamentals_history.return_value = pd.DataFrame([{"raw_json": None}])
        result = resolve_target_description("NIO", historical_store=store, descriptions_path=path)
        assert result is None

    def test_malformed_raw_json_returns_none_not_raise(self, tmp_path):
        path = self._yaml(tmp_path, targets={})
        store = MagicMock()
        import pandas as pd
        store.get_fundamentals_history.return_value = pd.DataFrame([{"raw_json": "{not valid json"}])
        result = resolve_target_description("NIO", historical_store=store, descriptions_path=path)
        assert result is None

    def test_store_exception_returns_none_not_raise(self, tmp_path):
        path = self._yaml(tmp_path, targets={})
        store = MagicMock()
        store.get_fundamentals_history.side_effect = RuntimeError("db down")
        result = resolve_target_description("NIO", historical_store=store, descriptions_path=path)
        assert result is None

    def test_as_of_omitted_uses_get_fundamentals_history_unchanged(self, tmp_path):
        """Regression: today's default (as_of=None, every existing caller)
        must stay byte-identical -- most-recent-row lookup via
        get_fundamentals_history, never the point-in-time path."""
        path = self._yaml(tmp_path, targets={})
        store = MagicMock()
        import pandas as pd
        store.get_fundamentals_history.return_value = pd.DataFrame([
            {"raw_json": json.dumps({"longBusinessSummary": "Makes electric vehicles."})}
        ])
        result = resolve_target_description("NIO", historical_store=store, descriptions_path=path)
        assert result == "Makes electric vehicles."
        store.get_fundamentals_raw_json_asof.assert_not_called()

    def test_as_of_given_uses_point_in_time_lookup_not_most_recent(self, tmp_path):
        """Regression (secondary audit, 2026-08-24 -- see
        docs/known_issues/sector_selection_similarity_lookahead.md): passing
        as_of must route through the point-in-time
        get_fundamentals_raw_json_asof(symbol, as_of) lookup, never the
        unconditional-most-recent get_fundamentals_history path (which would
        leak a FUTURE business description into a past-dated backtest
        replay)."""
        from datetime import datetime, timezone
        path = self._yaml(tmp_path, targets={})
        store = MagicMock()
        store.get_fundamentals_raw_json_asof.return_value = json.dumps(
            {"longBusinessSummary": "2015-era description."}
        )
        as_of = datetime(2015, 6, 1, tzinfo=timezone.utc)

        result = resolve_target_description(
            "NIO", historical_store=store, descriptions_path=path, as_of=as_of,
        )

        assert result == "2015-era description."
        store.get_fundamentals_raw_json_asof.assert_called_once_with("NIO", as_of)
        store.get_fundamentals_history.assert_not_called()

    def test_as_of_given_no_pit_row_returns_none_not_a_future_description(self, tmp_path):
        """A symbol with no fundamentals row whose report_date <= as_of
        (e.g. it only has data from AFTER the scored date) must resolve to
        None, never silently fall back to whatever's most recently cached --
        that fallback is exactly the lookahead leak this fix closes."""
        from datetime import datetime, timezone
        path = self._yaml(tmp_path, targets={})
        store = MagicMock()
        store.get_fundamentals_raw_json_asof.return_value = None
        as_of = datetime(2015, 6, 1, tzinfo=timezone.utc)

        result = resolve_target_description(
            "NIO", historical_store=store, descriptions_path=path, as_of=as_of,
        )

        assert result is None
        store.get_fundamentals_history.assert_not_called()
