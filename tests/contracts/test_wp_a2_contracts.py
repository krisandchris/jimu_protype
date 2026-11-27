import json
from copy import deepcopy
from decimal import Decimal
from itertools import permutations
from pathlib import Path

import pytest
from pydantic import ValidationError

from jimu.contracts import (
    BundleCategoryCatalog,
    N06ErrorCode,
    N06ImageFailure,
    N06RawEnvelope,
    N06RawModelOutput,
    N06RequestContext,
    NonViolationMappingPolicy,
    RawTargetJEVResult,
    RuntimeTargetReferenceCatalog,
    TargetJEVJudgment,
    TargetValidationOutcome,
    ToolFactEvidenceRef,
    VisualRegionRef,
    resolve_top_verdict,
    validate_n06_output,
)
from jimu.contracts._golden import sample_context

FIXTURES = Path(__file__).resolve().parents[2] / "docs/contracts/fixtures/wp_a2"


def case_bytes(case: str, side: str = "input") -> bytes:
    return (FIXTURES / f"case_{case}.{side}.json").read_bytes()


def empty_result(index: int = 0) -> dict:
    return {
        "target_ref_index": index,
        "ranked_categories": [],
        "non_violation_score": 1,
    }


@pytest.mark.parametrize("value", [0, 1, 0.34, 1.0])
def test_wire_accepts_json_numbers_and_exports_number_schema(value: float) -> None:
    target = RawTargetJEVResult.model_validate(
        {**empty_result(), "non_violation_score": value}
    )
    assert type(target.non_violation_score) is float
    assert target.non_violation_score == value
    schema = N06RawModelOutput.model_json_schema()
    props = schema["$defs"]["RawRankedCategory"]["properties"]
    assert set(props) == {"category_index", "score", "reasoning", "evidence"}
    assert (
        props["score"]["type"],
        props["score"]["minimum"],
        props["score"]["maximum"],
    ) == ("number", 0, 1)
    assert all(d["additionalProperties"] is False for d in schema["$defs"].values())


@pytest.mark.parametrize(
    "value", [True, "0.5", Decimal(".5"), float("nan"), float("inf"), -0.01, 1.01]
)
def test_wire_rejects_non_json_or_invalid_scores(value: object) -> None:
    with pytest.raises(ValidationError):
        RawTargetJEVResult.model_validate(
            {**empty_result(), "non_violation_score": value}
        )


@pytest.mark.parametrize("case", ["04", "05"])
def test_target_schema_failure_is_local_and_preserves_contract_causes(
    case: str,
) -> None:
    data = case_bytes(case)
    N06RawEnvelope.model_validate_json(data)
    with pytest.raises(ValidationError) as exc:
        N06RawModelOutput.model_validate_json(data)
    if case == "05":
        assert {
            err["loc"][-1]
            for err in exc.value.errors()
            if err["type"] == "extra_forbidden"
        } == {"target_instance_id", "evidence_completeness"}
    (result,) = validate_n06_output(data, sample_context())
    assert result == TargetValidationOutcome.model_validate_json(
        case_bytes(case, "expected")
    )


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("{", "n06_json_parse_failed"),
        ('{"results":[]}', "n06_schema_validation_failed"),
        ('{"results":[null]}', "n06_schema_validation_failed"),
        ('{"results":[{}]}', "n06_target_reference_invalid"),
        ('{"results":[{"target_ref_index":true}]}', "n06_target_reference_invalid"),
        ('{"results":[{"target_ref_index":2}]}', "n06_target_reference_invalid"),
        ('{"results":[{}],"extra":1}', "n06_schema_validation_failed"),
    ],
)
def test_unreconcilable_envelopes_fail_the_image(raw: str, code: str) -> None:
    with pytest.raises(N06ImageFailure) as exc:
        validate_n06_output(raw, sample_context())
    assert (exc.value.failure_code, exc.value.error_code.value) == ("N06_FAILED", code)


def test_truncated_output_fails_even_if_it_looks_parseable() -> None:
    with pytest.raises(N06ImageFailure) as exc:
        validate_n06_output(case_bytes("01"), sample_context(), output_truncated=True)
    assert exc.value.error_code == N06ErrorCode.N06_OUTPUT_TRUNCATED


@pytest.mark.parametrize("duplicate", [True, False])
def test_bad_missing_and_good_targets_are_reconciled_independently(
    duplicate: bool,
) -> None:
    data = sample_context().model_dump()
    entries = data["target_catalog"]["entries"]
    entries.extend(
        {**entries[0], "target_ref_index": i, "target_instance_id": f"other-{i}"}
        for i in (1, 2)
    )
    ctx = N06RequestContext.model_validate(data)
    bad = json.loads(case_bytes("05"))["results"][0]
    wire = [empty_result(1), bad, bad] if duplicate else [empty_result(1), bad]
    failed, ok, missing = validate_n06_output(json.dumps({"results": wire}), ctx)
    assert [r.status for r in (failed, ok, missing)] == [
        "JEV_FAILED",
        "SUCCEEDED",
        "JEV_MISSING_TARGET",
    ]
    assert failed.error_codes == [
        N06ErrorCode.N06_DUPLICATE_TARGET
        if duplicate
        else N06ErrorCode.N06_SCHEMA_VALIDATION_FAILED
    ]
    assert missing.error_codes == [N06ErrorCode.N06_TARGET_MISSING]
    assert ok.jev_judgment.top_verdict == "NON_VIOLATION"


def test_catalog_indices_ids_and_rules_are_unique() -> None:
    data = sample_context().category_catalog.model_dump()
    for field in ("category_id", "category_index", "rule_ref"):
        invalid = deepcopy(data)
        invalid["entries"][0][field] = invalid["entries"][1][field]
        with pytest.raises(ValidationError):
            BundleCategoryCatalog.model_validate(invalid)
    target = sample_context().target_catalog.model_dump()
    for change in ({"target_ref_index": 7}, {"target_instance_id": "other"}):
        with pytest.raises(ValidationError):
            RuntimeTargetReferenceCatalog.model_validate(
                {
                    **target,
                    "entries": [*target["entries"], {**target["entries"][0], **change}],
                }
            )


def test_canonical_tie_break_and_preservation_of_original_positions() -> None:
    ctx = sample_context()
    wire = json.loads(case_bytes("01"))
    rows = wire["results"][0]["ranked_categories"]
    for row in rows:
        row["score"] = 0.70
    for order in permutations(rows):
        wire["results"][0]["ranked_categories"] = order
        (verdict,) = validate_n06_output(json.dumps(wire), ctx)
        j = verdict.jev_judgment
        assert [(r.rank, r.category_id) for r in j.raw_ranking] == [
            (1, "category_b"),
            (2, "category_c"),
            (3, "category_d"),
        ]
        assert j.active_ranking == j.raw_ranking[:2]
        assert all(
            order[r.model_position]["category_index"] == r.category_index
            for r in j.raw_ranking
        )
        assert len(j.evidence_refs) == 5
        assert TargetJEVJudgment.model_validate_json(j.model_dump_json()) == j


def test_scope_filter_is_category_local_and_precedes_top_k() -> None:
    data = sample_context().model_dump()
    data["active_evaluations"].pop(0)
    (result,) = validate_n06_output(
        case_bytes("01"), N06RequestContext.model_validate(data)
    )
    j = result.jev_judgment
    assert [r.category_index for r in j.raw_ranking] == [2, 3]
    assert [r.category_index for r in j.active_ranking] == [2]
    assert [(r.category_index, r.reason_code) for r in j.isolated_categories] == [
        (1, "category_out_of_active_scope")
    ]


@pytest.mark.parametrize(
    ("top1", "v", "margin", "expected"),
    [
        (0.75, 0.85, 0.10, "NON_VIOLATION"),
        (0.85, 0.75, 0.10, "VIOLATION"),
        (0.75, 0.75, 0, "UNCERTAIN"),
        (0.75, 0.74, 0.10, "UNCERTAIN"),
        (None, 0.80, 0.10, "NON_VIOLATION"),
        (None, 0.799, 0.10, "UNCERTAIN"),
    ],
)
def test_decimal_mapping_and_empty_ranking(
    top1: float | None, v: float, margin: float, expected: str
) -> None:
    ctx = sample_context()
    (sample,) = validate_n06_output(case_bytes("01"), ctx)
    ranking = (
        []
        if top1 is None
        else [sample.jev_judgment.active_ranking[0].model_copy(update={"score": top1})]
    )
    policy = NonViolationMappingPolicy.model_validate(
        {**ctx.non_violation_mapping_policy.model_dump(), "margin": margin}
    )
    verdict, audit = resolve_top_verdict(ranking, v, policy)
    assert verdict == audit.resolved_verdict == expected
    assert audit.top1_score == top1
    assert audit.active_ranking_empty == (top1 is None)


@pytest.mark.parametrize(
    "bad",
    [
        "duplicate_evidence",
        "unknown_evidence",
        "image",
        "target",
        "version",
        "duplicate_category",
    ],
)
def test_bad_catalog_references_fail_only_the_target(bad: str) -> None:
    data = sample_context().model_dump()
    wire = json.loads(case_bytes("01"))
    rows = wire["results"][0]["ranked_categories"]
    evidence = rows[0]["evidence"]
    obs = data["evidence_catalog"][3]["evidence_ref"]
    if bad == "duplicate_evidence":
        evidence["visual_evidence_indices"] = [0, 0]
    elif bad == "unknown_evidence":
        evidence["tool_evidence_indices"] = [90]
    elif bad == "image":
        obs["image_execution_id"] = "other"
    elif bad == "target":
        obs["target_instance_id"] = "other"
    elif bad == "version":
        obs["question_ref"]["content_hash"] = "other"
    else:
        rows[1]["category_index"] = rows[0]["category_index"]
    (result,) = validate_n06_output(
        json.dumps(wire), N06RequestContext.model_validate(data)
    )
    assert result.status == "JEV_FAILED"
    assert result.error_codes == [
        N06ErrorCode.N06_DUPLICATE_CATEGORY
        if bad == "duplicate_category"
        else N06ErrorCode.N06_EVIDENCE_REFERENCE_INVALID
    ]


@pytest.mark.parametrize(
    "bbox",
    [[1, 2, 0, 3], [0, 1, 1, 1], [1, 2], [0, 0, float("inf"), 1], [True, 0, 1, 1]],
)
def test_visual_evidence_requires_a_finite_ordered_rectangle(bbox: list) -> None:
    with pytest.raises(ValidationError):
        VisualRegionRef.model_validate(
            {
                "source": "visual",
                "image_execution_id": "i",
                "evidence_id": "r",
                "bbox_xyxy": bbox,
            }
        )


def test_global_tool_evidence_has_no_target_and_uses_a_locked_producer() -> None:
    data = sample_context().model_dump()
    producer = data["question_refs"][0]
    data["tool_refs"] = [producer]
    ref = {
        "source": "tool",
        "scope": "GLOBAL",
        "image_execution_id": data["target_catalog"]["image_execution_id"],
        "target_instance_id": None,
        "fact_id": "global-fact",
        "step_key": "step",
        "fact_key": "k",
        "tool_ref": producer,
    }
    data["evidence_catalog"].append({"evidence_index": 21, "evidence_ref": ref})
    wire = json.loads(case_bytes("01"))
    wire["results"][0]["ranked_categories"][0]["evidence"]["tool_evidence_indices"] = [
        21
    ]
    (success,) = validate_n06_output(
        json.dumps(wire), N06RequestContext.model_validate(data)
    )
    assert ToolFactEvidenceRef.model_validate(ref) in success.jev_judgment.evidence_refs
    for change in ({"target_instance_id": "fake"}, {"scope": "TARGET"}):
        with pytest.raises(ValidationError):
            ToolFactEvidenceRef.model_validate({**ref, **change})


@pytest.mark.parametrize(
    "bad", ["order", "rank", "active", "position", "audit", "evidence"]
)
def test_imported_runtime_judgment_must_be_canonical(bad: str) -> None:
    (outcome,) = validate_n06_output(case_bytes("01"), sample_context())
    record = outcome.jev_judgment.model_dump()
    if bad == "order":
        record["raw_ranking"].reverse()
    elif bad == "rank":
        record["raw_ranking"][0]["rank"] = 3
    elif bad == "active":
        record["active_ranking"].pop(0)
    elif bad == "position":
        record["raw_ranking"][1]["model_position"] = 0
    elif bad == "audit":
        record["mapping_audit"]["top1_score"] = 0.81
    else:
        record["evidence_refs"].pop()
    with pytest.raises(ValidationError):
        TargetJEVJudgment.model_validate(record)


@pytest.mark.parametrize(
    "code",
    ["n06_output_truncated", "n06_json_parse_failed", "category_out_of_active_scope"],
)
def test_target_outcome_rejects_image_errors_and_isolated_categories(code: str) -> None:
    data = json.loads(case_bytes("04", "expected"))
    data["error_codes"] = [code]
    with pytest.raises(ValidationError):
        TargetValidationOutcome.model_validate_json(json.dumps(data))


def test_outcome_accepts_exact_error_codes_in_decoded_json() -> None:
    data = json.loads(case_bytes("04", "expected"))
    assert TargetValidationOutcome.model_validate(
        data
    ) == TargetValidationOutcome.model_validate_json(case_bytes("04", "expected"))
    for invalid in ("N06_SCORE_OUT_OF_RANGE", b"n06_score_out_of_range", True):
        with pytest.raises(ValidationError):
            TargetValidationOutcome.model_validate({**data, "error_codes": [invalid]})


def test_both_policies_require_explicit_version_locks_and_parameters() -> None:
    context = sample_context()
    for policy, field in [
        (context.ranking_policy, "top_k"),
        (context.non_violation_mapping_policy, "margin"),
    ]:
        data = policy.model_dump()
        with pytest.raises(ValidationError):
            type(policy).model_validate({**data, "content_hash": ""})
        data.pop(field)
        with pytest.raises(ValidationError):
            type(policy).model_validate(data)
