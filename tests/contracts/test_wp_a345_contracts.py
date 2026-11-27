import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from jimu.contracts import (
    CategoryEvidenceCompleteness,
    ComparisonProtocolFailure,
    CompiledRuleFieldDependency,
    CompiledRuleToolDependency,
    N06RequestContext,
    ResolvedFactRef,
    RuleJudgment,
    TargetASTSummary,
    TargetComparisonResult,
    TopKMetrics,
    available_comparison_status,
    category_diff_type,
    validate_n06_output,
)
from jimu.contracts._golden import sample_context

GOLDEN = Path(__file__).resolve().parents[2] / "docs/contracts/fixtures/wp_a345"


def evidence_data() -> dict:
    source = json.loads((GOLDEN / "case_01.input.json").read_bytes())
    expected = json.loads((GOLDEN / "case_01.expected.json").read_bytes())
    return {
        **expected["results"][0],
        "rule_ref": source["categories"][0]["rule_ref"],
        "image_execution_id": source["image_execution_id"],
        "target_instance_id": source["target_instance_id"],
        "n04_final_attempt_id": None,
        "n05_execution_id": "final-N05",
    }


def test_compiled_dependency_and_runtime_fact_identities() -> None:
    producer = sample_context().question_refs[0]
    common = {"rule_ref": producer, "required": True, "source_ast_paths": ["$.root"]}
    field = CompiledRuleFieldDependency(
        **common, question_ref=producer, field_key="answer", target_scope="BOTH"
    )
    tool = CompiledRuleToolDependency(
        **common,
        tool_ref=producer,
        step_key="step",
        fact_key="answer",
        target_scope="GLOBAL",
    )
    for dep in (field, tool):
        with pytest.raises(ValidationError):
            type(dep).model_validate({**dep.model_dump(), "target_instance_id": "O1"})
    base = {
        "source": "tool",
        "image_execution_id": "image",
        "target_instance_id": None,
        "scope": "GLOBAL",
        "tool_ref": producer,
        "step_key": "step",
        "fact_key": "answer",
    }
    assert ResolvedFactRef.model_validate(base).target_instance_id is None
    for change in (
        {"scope": "TARGET"},
        {"target_instance_id": "O1"},
        {"source": "observation"},
        {"step_key": None},
    ):
        with pytest.raises(ValidationError):
            ResolvedFactRef.model_validate({**base, **change})


def test_evidence_status_counts_and_nullable_final_attempt() -> None:
    required = evidence_data()
    assert (
        CategoryEvidenceCompleteness.model_validate(required).n04_final_attempt_id
        is None
    )
    partial = deepcopy(required)
    partial.update(
        status="PARTIAL",
        required_dependency_count=0,
        required_failed_count=0,
        optional_dependency_count=1,
        optional_failed_count=1,
    )
    partial["unavailable_dependency_refs"][0]["required"] = False
    assert CategoryEvidenceCompleteness.model_validate(partial).status == "PARTIAL"
    with pytest.raises(ValidationError):
        CategoryEvidenceCompleteness.model_validate({**partial, "status": "COMPLETE"})
    with pytest.raises(ValidationError):
        CategoryEvidenceCompleteness.model_validate(
            {**required, "required_valid_count": 1}
        )


@pytest.mark.parametrize(
    "invalid", ["overlap", "duplicate", "sort", "fact_status", "required", "scope"]
)
def test_failure_lists_obey_identity_order_and_individual_status(invalid: str) -> None:
    data = evidence_data()
    refs = data["unavailable_dependency_refs"]
    if invalid in {"overlap", "duplicate", "sort"}:
        data.update(required_dependency_count=2, required_failed_count=2)
    if invalid == "overlap":
        data["missing_dependency_refs"].append({**refs[0], "fact_status": "MISSING"})
    elif invalid == "duplicate":
        refs.append(deepcopy(refs[0]))
    elif invalid == "sort":
        refs.append({**refs[0], "dependency_id": "aaa"})
    elif invalid == "fact_status":
        refs[0]["fact_status"] = "UNKNOWN"
    elif invalid == "required":
        refs[0]["required"] = False
    else:
        refs[0]["target_scope"] = "OBJECT"
    with pytest.raises(ValidationError):
        CategoryEvidenceCompleteness.model_validate(data)


def judgments(statuses: list[str]) -> tuple:
    ctx = sample_context()
    evaluations = ctx.active_evaluations[: len(statuses)]
    items = [
        RuleJudgment.model_validate(
            {
                "rule_ref": e.rule_ref,
                "category_id": e.category_id,
                "target_instance_id": e.target_instance_id,
                "status": status,
                "consumed_fact_refs": [],
                "unavailable_fact_refs": [],
                "rule_trace_ref": f"trace/{e.category_id}",
                "error_code": "REQUIRED_FACT_UNAVAILABLE"
                if status == "FAILED"
                else None,
                "image_execution_id": ctx.target_catalog.image_execution_id,
            }
        )
        for e, status in zip(evaluations, statuses, strict=True)
    ]
    return (
        ctx.target_catalog.image_execution_id,
        ctx.target_catalog.entries[0].target_instance_id,
        evaluations,
        items,
    )


def ast_summary(*statuses: str) -> TargetASTSummary:
    return TargetASTSummary.from_judgments(*judgments(list(statuses)))


def test_reconciliation_keeps_successful_and_failed_partitions_and_trace_order() -> (
    None
):
    image, target, evaluations, items = judgments(
        ["NON_TRIGGERED", "FAILED", "UNCERTAIN"]
    )
    s = TargetASTSummary.from_judgments(
        image, target, evaluations, list(reversed(items))
    )
    assert s.aggregation_status == "PARTIAL_FAILED"
    assert [
        s.non_triggered_categories,
        s.failed_categories,
        s.uncertain_categories,
    ] == [["category_b"], ["category_c"], ["category_d"]]
    assert s.rule_judgment_refs == [
        "trace/category_b",
        "trace/category_c",
        "trace/category_d",
    ]
    for broken in (
        [*items, items[0]],
        items[1:],
        [items[0].model_copy(update={"image_execution_id": "other"}), *items[1:]],
        [items[0].model_copy(update={"rule_ref": items[1].rule_ref}), *items[1:]],
    ):
        with pytest.raises(ValueError):
            TargetASTSummary.from_judgments(image, target, evaluations, broken)
    with pytest.raises(ValueError):
        TargetASTSummary.from_judgments(image, target, [], [])


@pytest.mark.parametrize(
    "field",
    [
        "triggered_categories",
        "failed_categories",
        "aggregation_status",
        "category_coverage",
        "rule_judgment_refs",
    ],
)
def test_summary_rejects_partition_count_status_or_trace_mismatch(field: str) -> None:
    data = ast_summary("TRIGGERED", "FAILED", "TRIGGERED").model_dump()
    if field == "triggered_categories":
        data[field].reverse()
    elif field == "failed_categories":
        data[field] = ["category_b"]
    elif field == "aggregation_status":
        data[field] = "ALL_SUCCEEDED"
    elif field == "category_coverage":
        data[field]["active_count"] = 4
    else:
        data[field].pop()
    with pytest.raises(ValidationError):
        TargetASTSummary.model_validate(data)


def jev_judgment(indices: list[int], v: float, score: float = 0.85):
    context = sample_context().model_dump()
    context["ranking_policy"]["min_candidate_score"] = 0.0
    wire = {
        "results": [
            {
                "target_ref_index": 0,
                "non_violation_score": v,
                "ranked_categories": [
                    {
                        "category_index": i,
                        "score": score,
                        "reasoning": "",
                        "evidence": {
                            "visual_evidence_indices": [],
                            "observation_evidence_indices": [],
                            "tool_evidence_indices": [],
                        },
                    }
                    for i in indices
                ],
            }
        ]
    }
    (outcome,) = validate_n06_output(
        json.dumps(wire), N06RequestContext.model_validate(context)
    )
    assert outcome.jev_judgment is not None
    return outcome.jev_judgment


@pytest.mark.parametrize(
    ("indices", "v", "statuses", "expected"),
    [
        ([1], 0, ["FAILED"], "AST_FAILED"),
        ([1], 0, ["TRIGGERED", "FAILED"], "AST_PARTIAL_FAILED"),
        ([], 0.4, ["UNCERTAIN"], "BOTH_UNCERTAIN"),
        ([], 0.4, ["TRIGGERED"], "JEV_UNCERTAIN"),
        ([1], 0, ["UNCERTAIN"], "AST_UNCERTAIN"),
        ([], 1, ["NON_TRIGGERED"], "AGREE_NON_VIOLATION"),
        ([], 1, ["TRIGGERED"], "EXISTENCE_CONFLICT"),
        ([1], 0, ["NON_TRIGGERED"], "EXISTENCE_CONFLICT"),
        ([2], 0, ["TRIGGERED", "NON_TRIGGERED"], "CATEGORY_CONFLICT"),
        ([1], 0, ["TRIGGERED", "TRIGGERED"], "CATEGORY_SET_PARTIAL_MATCH"),
        ([1], 0, ["TRIGGERED", "UNCERTAIN"], "AGREE_TOP1"),
        ([2, 1], 0, ["TRIGGERED", "NON_TRIGGERED"], "AGREE_TOPK_RANK_DIFF"),
    ],
)
def test_comparison_priorities(
    indices: list[int], v: float, statuses: list[str], expected: str
) -> None:
    assert (
        available_comparison_status(jev_judgment(indices, v), ast_summary(*statuses))
        == expected
    )


def test_non_violation_mapping_with_active_candidate_hits_p3_before_uncertainty() -> (
    None
):
    jev = jev_judgment([1], 0.85, score=0.55)
    assert jev.top_verdict == "NON_VIOLATION" and jev.active_ranking
    assert available_comparison_status(jev, ast_summary("UNCERTAIN")) == "JEV_FAILED"


def closure_data() -> dict:
    s = ast_summary("TRIGGERED", "NON_TRIGGERED")
    j = jev_judgment([1, 2], 0)
    identity = {
        "image_execution_id": s.image_execution_id,
        "target_instance_id": s.target_instance_id,
    }
    provenance = {
        **identity,
        "bundle_digest": j.bundle_digest,
        "category_catalog_digest": j.category_catalog_digest,
        "planner_version": "v1",
        "target_planning_snapshot_hash": "snapshot",
        "jev_judgment_ref": "j",
        "ast_summary_ref": "s",
        "evidence_completeness_refs": [],
        "n04_final_attempt_id": None,
        "n05_execution_id": None,
        "compiled_tool_plan_digest": None,
        "runtime_tool_plan_hash": None,
    }
    return {
        **identity,
        "comparison_status": "AGREE_TOPK_RANK_DIFF",
        "ast_summary": s.model_dump(),
        "jev_judgment": j.model_dump(),
        "provenance": provenance,
        "evidence_completeness_by_category": [],
        "topk_metrics": TopKMetrics.from_categories(
            ["category_b", "category_c"], ["category_b"]
        ).model_dump(),
        "per_category_diffs": [
            {
                "category_id": r.category_id,
                "jev_rank": r.rank,
                "jev_score": r.score,
                "ast_status": s.category_statuses[r.category_id],
                "evidence_completeness_status": None,
                "rule_judgment_ref": f"trace/{r.category_id}",
                "diff_type": kind,
            }
            for r, kind in zip(
                j.active_ranking,
                ("AGREED_TRIGGERED", "JEV_CANDIDATE_AST_NON_TRIGGERED"),
                strict=True,
            )
        ],
    }


def test_complete_comparison_round_trip_and_protocol_failure() -> None:
    result = TargetComparisonResult.model_validate(closure_data())
    assert (
        TargetComparisonResult.model_validate_json(result.model_dump_json()) == result
    )
    error = ComparisonProtocolFailure(
        image_execution_id="img",
        target_instance_id="target",
        error_code="PROVENANCE_MISMATCH",
        error_message="policy hash mismatch",
        source_refs=["snapshot", "j"],
    )
    with pytest.raises(ValidationError):
        TargetComparisonResult.model_validate(error.model_dump())


@pytest.mark.parametrize(
    "invalid",
    [
        "image",
        "digest",
        "reference_count",
        "diff",
        "sort",
        "duplicate",
        "metrics",
        "status",
        "final_decision",
        "needs_hitl",
    ],
)
def test_closure_rejects_inconsistent_output(invalid: str) -> None:
    data = closure_data()
    if invalid == "image":
        data["ast_summary"]["image_execution_id"] = "other"
    elif invalid == "digest":
        data["provenance"]["bundle_digest"] = "other"
    elif invalid == "reference_count":
        data["provenance"]["evidence_completeness_refs"] = ["extra"]
    elif invalid == "diff":
        data["per_category_diffs"].pop()
    elif invalid == "sort":
        data["per_category_diffs"].reverse()
    elif invalid == "duplicate":
        data["per_category_diffs"].append(data["per_category_diffs"][0])
    elif invalid == "metrics":
        data["topk_metrics"] = None
    elif invalid == "status":
        data["comparison_status"] = "AGREE_TOP1"
    else:
        data[invalid] = False
    with pytest.raises(ValidationError):
        TargetComparisonResult.model_validate(data)


@pytest.mark.parametrize(
    "status", ["BOTH_MISSING", "AST_MISSING", "JEV_MISSING_TARGET", "JEV_FAILED"]
)
def test_missing_and_failed_sides_cannot_publish_metrics(status: str) -> None:
    data = closure_data()
    data.update(comparison_status=status, topk_metrics=None)
    if status in {"BOTH_MISSING", "AST_MISSING"}:
        data["ast_summary"] = None
        data["provenance"]["ast_summary_ref"] = None
    if status != "AST_MISSING":
        data["jev_judgment"] = None
        data["provenance"]["jev_judgment_ref"] = None
    if status == "BOTH_MISSING":
        data["per_category_diffs"] = []
    elif status == "AST_MISSING":
        for d in data["per_category_diffs"]:
            d.update(ast_status=None, rule_judgment_ref=None, diff_type="JEV_ONLY")
    else:
        for d in data["per_category_diffs"]:
            d.update(
                jev_rank=None, jev_score=None, diff_type="AST_ONLY_JEV_UNAVAILABLE"
            )
    assert TargetComparisonResult.model_validate(data).topk_metrics is None
    data["topk_metrics"] = TopKMetrics.from_categories([], []).model_dump()
    with pytest.raises(ValidationError):
        TargetComparisonResult.model_validate(data)


def test_topk_empty_and_all_covered_differs_from_any_hit() -> None:
    empty = TopKMetrics.from_categories([], [])
    assert (
        empty.top1_hit,
        empty.topk_hit,
        empty.topk_coverage_ratio,
        empty.topk_precision_ratio,
    ) == (None,) * 4
    m = TopKMetrics.from_categories(["a", "b"], ["a", "c"])
    assert (m.top1_hit, m.topk_hit, m.topk_coverage_ratio, m.topk_precision_ratio) == (
        True,
        False,
        0.5,
        0.5,
    )
    with pytest.raises(ValidationError):
        TopKMetrics.model_validate({**m.model_dump(), "intersection_count": 3})
    assert category_diff_type(False, False, "FAILED") == "AST_FAILED"
    assert category_diff_type(True, False, "TRIGGERED") == "AST_TRIGGERED_JEV_MISSED"


@pytest.mark.parametrize(
    ("statuses", "comparison"),
    [
        (["FAILED", "FAILED"], "AST_FAILED"),
        (["TRIGGERED", "FAILED"], "AST_PARTIAL_FAILED"),
    ],
)
def test_ast_failure_preserves_both_sides_and_all_category_diffs(
    statuses: list[str], comparison: str
) -> None:
    data = closure_data()
    summary = ast_summary(*statuses)
    data.update(
        ast_summary=summary.model_dump(),
        comparison_status=comparison,
        topk_metrics=None,
    )
    for diff in data["per_category_diffs"]:
        status = summary.category_statuses[diff["category_id"]]
        diff.update(
            ast_status=status,
            diff_type="AST_FAILED" if status == "FAILED" else "AGREED_TRIGGERED",
        )
    result = TargetComparisonResult.model_validate(data)
    assert result.ast_summary == summary and result.jev_judgment is not None
    assert [d.ast_status for d in result.per_category_diffs] == statuses
    with pytest.raises(ValidationError):
        TargetComparisonResult.model_validate(
            {**data, "topk_metrics": TopKMetrics.from_categories([], [])}
        )


def test_p0_rejects_misaligned_target_and_rule_versions() -> None:
    j = jev_judgment([1], 0.1)
    with pytest.raises(ValueError, match="identity"):
        available_comparison_status(
            j,
            ast_summary("TRIGGERED").model_copy(update={"target_instance_id": "other"}),
        )
    data = closure_data()
    item = evidence_data()
    item["rule_ref"] = {
        **data["jev_judgment"]["raw_ranking"][0]["rule_ref"],
        "content_hash": "different",
    }
    item.update(
        category_id="category_b",
        image_execution_id=data["image_execution_id"],
        target_instance_id=data["target_instance_id"],
    )
    data["evidence_completeness_by_category"] = [item]
    data["provenance"]["evidence_completeness_refs"] = ["evidence/b"]
    data["per_category_diffs"][0]["evidence_completeness_status"] = item["status"]
    with pytest.raises(ValidationError, match="rule versions"):
        TargetComparisonResult.model_validate(data)
