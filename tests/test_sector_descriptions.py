"""Tests for data/sector_descriptions.yaml -- pins its key set as a
superset of forecasting/data/ticker_sectors.csv's distinct sectors AND
forecasting/sector_configs.json's sector_configs keys. A mismatch here
would silently produce empty joins and an all-NaN correlation matrix that
looks like a data outage rather than a typo."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from data.sector_embeddings import load_sector_descriptions

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TICKER_SECTORS_CSV = _REPO_ROOT / "forecasting" / "data" / "ticker_sectors.csv"
_SECTOR_CONFIGS_JSON = _REPO_ROOT / "forecasting" / "sector_configs.json"


def _csv_distinct_sectors() -> set:
    with open(_TICKER_SECTORS_CSV, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return {row["sector"].strip() for row in reader if row.get("sector")}


def _sector_configs_keys() -> set:
    with open(_SECTOR_CONFIGS_JSON) as f:
        data = json.load(f)
    return set(data["sector_configs"].keys())


class TestSectorDescriptionsKeySuperset:
    def test_loads_successfully(self):
        descriptions = load_sector_descriptions()
        assert descriptions, "sector_descriptions.yaml must load at least one sector"

    def test_superset_of_ticker_sectors_csv(self):
        descriptions = load_sector_descriptions()
        missing = _csv_distinct_sectors() - set(descriptions.keys())
        assert not missing, f"sector_descriptions.yaml missing sectors from ticker_sectors.csv: {missing}"

    def test_superset_of_sector_configs_json(self):
        descriptions = load_sector_descriptions()
        missing = _sector_configs_keys() - set(descriptions.keys())
        assert not missing, f"sector_descriptions.yaml missing sectors from sector_configs.json: {missing}"

    def test_every_description_is_non_empty_text(self):
        descriptions = load_sector_descriptions()
        for sector, description in descriptions.items():
            assert isinstance(description, str)
            assert len(description.strip()) > 20, f"{sector!r} description looks too short/placeholder-y"

    def test_missing_file_degrades_to_empty_dict(self, tmp_path):
        result = load_sector_descriptions(path=tmp_path / "does_not_exist.yaml")
        assert result == {}

    def test_malformed_yaml_degrades_to_empty_dict(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("sectors: [this, is, not, a, mapping")  # unclosed bracket
        result = load_sector_descriptions(path=bad)
        assert result == {}
