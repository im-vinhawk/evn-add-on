"""Diagnostics must never carry a customer code: the repo is public and users attach the file to issues."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


models = _load("evn_vietnam_models_diagnostics", "models.py")
const = _load("evn_vietnam_const_diagnostics", "const.py")

PRIMARY = "PB01234567890"
OTHER = "PB09876543210"
ENTRY_DATA = {
    "username": "user",
    "password": "pw",
    "access_token": "tok",
    "refresh_token": "rtok",
    "device_id": "0f0f0f0f-0000-0000-0000-000000000000",
    "primary_customer_code": PRIMARY,
    "current_customer_code": OTHER,
    "linked_customers": {PRIMARY: f"{PRIMARY}001", OTHER: f"{OTHER}002"},
}


def test_no_customer_code_survives_anywhere() -> None:
    dumped = json.dumps(models.anonymize_customer_codes(ENTRY_DATA))
    for code in (PRIMARY, OTHER):
        assert code not in dumped
        assert code.lower() not in dumped.lower()


def test_aliases_are_stable_across_fields() -> None:
    out = models.anonymize_customer_codes(ENTRY_DATA)
    primary_alias = out["primary_customer_code"]
    current_alias = out["current_customer_code"]
    assert primary_alias != current_alias
    assert set(out["linked_customers"]) == {primary_alias, current_alias}
    # the meter-point suffix stays readable so roster bugs can still be diagnosed
    assert out["linked_customers"][primary_alias] == f"{primary_alias}+001"
    assert out["linked_customers"][current_alias] == f"{current_alias}+002"


def test_unrelated_fields_and_input_are_untouched() -> None:
    before = json.dumps(ENTRY_DATA, sort_keys=True)
    out = models.anonymize_customer_codes(ENTRY_DATA)
    assert json.dumps(ENTRY_DATA, sort_keys=True) == before
    assert out["username"] == "user" and out["device_id"] == ENTRY_DATA["device_id"]


def test_meter_point_not_derived_from_its_code_is_redacted() -> None:
    out = models.anonymize_customer_codes({"linked_customers": {PRIMARY: "PX99999999999999"}})
    assert list(out["linked_customers"].values()) == ["**REDACTED**"]


def test_redact_keys_cover_credentials_and_device_id() -> None:
    assert {"username", "password", "access_token", "refresh_token", "device_id"} <= set(const.DIAGNOSTICS_TO_REDACT)
