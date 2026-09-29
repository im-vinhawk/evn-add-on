"""Nickname normalization stays HA-free and defensive."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "evn_vietnam" / "models.py"
SPEC = importlib.util.spec_from_file_location("evn_vietnam_models_aliases", MODULE_PATH)
assert SPEC and SPEC.loader
models = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = models  # dataclasses resolve annotations via sys.modules
SPEC.loader.exec_module(models)


def test_alias_is_trimmed_and_limited_to_30_chars() -> None:
    assert models.normalize_alias("  Nhà chính  ") == "Nhà chính"
    assert len(models.normalize_alias("x" * 50)) == 30


def test_alias_keeps_vietnamese_and_drops_control_and_markup_chars() -> None:
    assert models.normalize_alias("Quán Cà Phê Đà Lạt") == "Quán Cà Phê Đà Lạt"
    assert models.normalize_alias("a\x00b\nc<d>e") == "abcde"


def test_alias_non_string_or_blank_is_empty() -> None:
    assert models.normalize_alias(None) == ""
    assert models.normalize_alias("   ") == ""
    assert models.normalize_alias(12) == ""


def test_aliases_drop_unknown_codes_and_blanks_and_uppercase_keys() -> None:
    raw = {"pb000001": " Nhà chính ", "PB000002": "", "PB000009": "Lạ", "PB000003": None}
    assert models.normalize_aliases(raw, ["PB000001", "PB000002", "PB000003"]) == {"PB000001": "Nhà chính"}


def test_aliases_non_mapping_is_empty() -> None:
    assert models.normalize_aliases(None, ["PB000001"]) == {}
    assert models.normalize_aliases("PB000001", ["PB000001"]) == {}
