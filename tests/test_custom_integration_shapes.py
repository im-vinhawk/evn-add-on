"""describe_shape must expose key names and value types, never values."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_shapes_test"
# calculation imports the tariff module, so it is loaded as part of a package.
sys.modules[PACKAGE] = types.ModuleType(PACKAGE)
sys.modules[PACKAGE].__path__ = [str(INTEGRATION_DIR)]
SPEC = importlib.util.spec_from_file_location(f"{PACKAGE}.calculation", INTEGRATION_DIR / "calculation.py")
assert SPEC and SPEC.loader
calculation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = calculation
SPEC.loader.exec_module(calculation)


def test_scalar_types_and_digit_counts() -> None:
    shape = calculation.describe_shape(
        {"THANG": 8, "TONG_TIEN": 123456.0, "X": "text", "D": "05/09/2026", "ISO": "2026-09-05", "OK": True, "N": None}
    )
    assert shape["THANG"] == "int/1d"
    assert shape["TONG_TIEN"] == "float/6d"
    assert shape["X"] == "str/len4"
    assert shape["D"] == "str/len10/date-dmy"
    assert shape["ISO"] == "str/len10/date-iso"
    assert shape["OK"] == "bool"
    assert shape["N"] == "null"


def test_shape_never_contains_raw_values() -> None:
    row = {"MA_KHANG": "PB000001", "TONG_TIEN": 123456.0, "SO": 987654, "TEN": "Nguyen Van A"}
    dumped = json.dumps(calculation.describe_shape(row))
    for secret in ("PB000001", "123456", "987654", "Nguyen"):
        assert secret not in dumped


def test_nested_containers_describe_children_only() -> None:
    shape = calculation.describe_shape({"L": [{"A": 12}, {"B": "x"}], "E": [], "D": {"K": 3.5}})
    assert shape["L"] == "list/2"
    assert shape["E"] == "list/0"
    assert shape["D"] == {"K": "float/1d"}


def test_non_mapping_input_returns_empty() -> None:
    assert calculation.describe_shape(None) == {}
    assert calculation.describe_shape("PB000001") == {}
