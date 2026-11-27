import json
from decimal import Decimal
from math import inf, nan

import pytest
from pydantic import ValidationError

from jimu.contracts.base import AssetVersionRef, FiniteScore, StrictModel
from jimu.contracts.verify_schemas import export_schemas

ASSET_REF = {"asset_id": "rule_a", "version": "1.0.0", "content_hash": "sha256:rulea"}


class Scored(StrictModel):
    score: FiniteScore


class Measurement(StrictModel):
    value: float


def test_asset_version_ref_round_trip() -> None:
    ref = AssetVersionRef.model_validate(ASSET_REF)
    assert ref.model_dump(mode="json") == ASSET_REF
    assert AssetVersionRef.model_validate_json(ref.model_dump_json()) == ref


@pytest.mark.parametrize("field", ASSET_REF)
def test_asset_ref_requires_each_part_of_the_version_lock(field: str) -> None:
    with pytest.raises(ValidationError):
        AssetVersionRef.model_validate(
            {k: v for k, v in ASSET_REF.items() if k != field}
        )


@pytest.mark.parametrize("field", ASSET_REF)
@pytest.mark.parametrize("invalid", ["", None, 1, b"value"])
def test_asset_ref_requires_nonempty_strict_strings(
    field: str, invalid: object
) -> None:
    with pytest.raises(ValidationError):
        AssetVersionRef.model_validate({**ASSET_REF, field: invalid})


def test_strict_model_forbids_extra_fields_and_mutation() -> None:
    with pytest.raises(ValidationError) as extra:
        AssetVersionRef.model_validate({**ASSET_REF, "target_instance_id": "O001"})
    assert extra.value.errors()[0]["type"] == "extra_forbidden"

    ref = AssetVersionRef.model_validate(ASSET_REF)
    with pytest.raises(ValidationError) as frozen:
        ref.version = "2.0.0"
    assert frozen.value.errors()[0]["type"] == "frozen_instance"


@pytest.mark.parametrize("value", [nan, inf, -inf, "0.5"])
def test_strict_model_float_fields_are_strict_and_finite(value: object) -> None:
    with pytest.raises(ValidationError):
        Measurement.model_validate({"value": value})


@pytest.mark.parametrize("value", [0, 1, 0.0, 0.375, 1.0])
def test_score_preserves_valid_numbers_in_python_and_json(value: int | float) -> None:
    data = {"score": value}
    for model in (
        Scored.model_validate(data),
        Scored.model_validate_json(json.dumps(data)),
    ):
        assert model.score == value
        assert type(model.score) is type(value)


@pytest.mark.parametrize(
    "value", [-1, 2, -0.0001, 1.0001, True, False, "0", "0.5", None, nan, inf, -inf]
)
def test_score_rejects_invalid_python_and_wire_values(value: object) -> None:
    data = {"score": value}
    with pytest.raises(ValidationError):
        Scored.model_validate(data)
    with pytest.raises(ValidationError):
        Scored.model_validate_json(json.dumps(data))


def test_score_rejects_implicit_python_float_conversion() -> None:
    with pytest.raises(ValidationError):
        Scored.model_validate({"score": Decimal("0.5")})


def test_exported_schemas_describe_the_base_types() -> None:
    schemas = export_schemas()
    assert schemas["jimu.contracts.base.StrictModel"]["additionalProperties"] is False
    ref = schemas["jimu.contracts.base.AssetVersionRef"]
    assert ref["additionalProperties"] is False
    assert set(ref["required"]) == set(ref["properties"]) == set(ASSET_REF)
    branches = schemas["jimu.contracts.base.FiniteScore"]["anyOf"]
    assert {(b["type"], b["minimum"], b["maximum"]) for b in branches} == {
        ("integer", 0, 1),
        ("number", 0, 1),
    }
