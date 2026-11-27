import json
import shutil
from pathlib import Path

import pytest
from pydantic import BaseModel

from jimu import contracts
from jimu.contracts.verify_schemas import export_schemas, verify_fixtures

CONTRACTS = Path(__file__).resolve().parents[2] / "docs/contracts"


def test_all_public_models_export_and_all_eleven_pairs_verify() -> None:
    schemas = export_schemas()
    for name in contracts.__all__:
        model = getattr(contracts, name)
        if isinstance(model, type) and issubclass(model, BaseModel):
            assert schemas[f"{model.__module__}.{name}"] == model.model_json_schema()
    assert verify_fixtures(CONTRACTS) == 11


def test_policy_projection_inputs_match_the_values_in_section_nine() -> None:
    path = CONTRACTS / "fixtures/wp_a2"
    mapping = json.loads((path / "case_02.input.json").read_bytes())
    assert mapping == {
        "active_ranking": [{"category_id": "goods_blocking_road", "score": 0.55}],
        "non_violation_score": 0.85,
        "margin": 0.10,
        "empty_ranking_non_violation_threshold": 0.80,
    }
    tied = json.loads((path / "case_03.input.json").read_bytes())
    assert sorted((r["category_id"], r["score"]) for r in tied) == [
        ("goods_blocking_road", 0.90),
        ("motor_vehicle_illegal_parking", 0.90),
        ("nonmotor_vehicle_illegal_parking", 0.90),
    ]


@pytest.mark.parametrize(
    ("suite", "filename", "key", "wrong"),
    [
        ("wp_a2", "case_01.expected.json", "active_ranking_count", 3),
        ("wp_a2", "case_02.expected.json", "top_verdict", "VIOLATION"),
        ("wp_a345", "case_04.expected.json", "comparison_status", "AGREE_TOP1"),
    ],
)
def test_verifier_fails_on_golden_drift(
    tmp_path: Path, suite: str, filename: str, key: str, wrong: object
) -> None:
    copy = tmp_path / "contracts"
    shutil.copytree(CONTRACTS, copy)
    path = copy / "fixtures" / suite / filename
    data = json.loads(path.read_bytes())
    data[key] = wrong
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Case"):
        verify_fixtures(copy)
