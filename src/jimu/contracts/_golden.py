"""Executable golden examples with explicit fixture-only catalogs and policies."""

import json
import re
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .base import AssetVersionRef, StrictModel
from .evaluation import RuleTargetEvaluation
from .wp_a2_runtime import (
    BundleCategoryCatalog,
    BundleCategoryEntry,
    N06RankingPolicy,
    NonViolationMappingPolicy,
    ObservationFactEvidenceRef,
    ResolvedCategoryCandidate,
    RuntimeRankedCategory,
    RuntimeTargetReferenceCatalog,
    TargetReferenceEntry,
    VisualRegionRef,
    resolve_top_verdict,
)
from .wp_a2_validation import (
    IndexedEvidence,
    N06RequestContext,
    TargetValidationOutcome,
    validate_n06_output,
)
from .wp_a2_wire import FiniteUnitFloat, N06RawModelOutput
from .wp_a3_evidence import _CategoryEvidenceDetails
from .wp_a4_ast import RuleJudgmentStatus, TargetASTSummary
from .wp_a5_comparison import (
    _NO_METRICS,
    TopKMetrics,
    _status_for_categories,
    category_diff_type,
)


class _ScoredCategory(StrictModel):
    category_id: str
    score: FiniteUnitFloat


class _MappingExample(StrictModel):
    active_ranking: list[_ScoredCategory]
    non_violation_score: FiniteUnitFloat
    margin: FiniteUnitFloat
    empty_ranking_non_violation_threshold: FiniteUnitFloat


def _sample_ref(asset_id: str) -> AssetVersionRef:
    return AssetVersionRef(
        asset_id=asset_id, version="fixture-v1", content_hash=f"fixture:{asset_id}"
    )


def sample_context() -> N06RequestContext:
    """Explicit catalogs omitted by WP-A2 §9. Only indices 1, 2, 3 are in scope."""
    image, target = "fixture-image", "fixture-target"
    entries = [
        BundleCategoryEntry(
            category_index=i,
            category_id=f"category_{letter}",
            rule_ref=_sample_ref(f"rule_{letter}"),
        )
        for i, letter in enumerate("bcd", 1)
    ]
    question = _sample_ref("question")
    evidence = [
        IndexedEvidence(
            evidence_index=i,
            evidence_ref=VisualRegionRef(
                source="visual",
                image_execution_id=image,
                evidence_id=f"region-{i}",
                bbox_xyxy=[0.0, 0.0, 1.0, 1.0],
            ),
        )
        for i in range(3)
    ]
    evidence.extend(
        IndexedEvidence(
            evidence_index=i,
            evidence_ref=ObservationFactEvidenceRef(
                source="observation",
                image_execution_id=image,
                fact_id=f"fact-{i}",
                target_instance_id=target,
                question_ref=question,
                field_key="field",
            ),
        )
        for i in range(2)
    )
    return N06RequestContext(
        bundle_digest="fixture-bundle",
        category_catalog=BundleCategoryCatalog(
            entries=entries, catalog_digest="fixture-categories"
        ),
        target_catalog=RuntimeTargetReferenceCatalog(
            image_execution_id=image,
            catalog_digest="fixture-targets",
            entries=[
                TargetReferenceEntry(
                    target_ref_index=0, target_instance_id=target, target_kind="OBJECT"
                )
            ],
        ),
        active_evaluations=[
            RuleTargetEvaluation(
                rule_ref=e.rule_ref,
                category_id=e.category_id,
                target_instance_id=target,
                target_kind="OBJECT",
                applicable_subject_ref=_sample_ref("subject"),
            )
            for e in entries
        ],
        evidence_catalog=evidence,
        question_refs=[question],
        tool_refs=[],
        ranking_policy=N06RankingPolicy(
            policy_id="fixture-ranking",
            version="1",
            content_hash="fixture:r",
            policy_description="WP-A2 Case 01",
            top_k=2,
            min_candidate_score=0.70,
            tie_break_rule="score_desc_category_id_asc",
        ),
        non_violation_mapping_policy=NonViolationMappingPolicy(
            policy_id="fixture-mapping",
            version="1",
            content_hash="fixture:m",
            policy_description="WP-A2 Case 02",
            mode="score_comparison",
            margin=0.10,
            empty_ranking_non_violation_threshold=0.80,
        ),
    )


def _blocks(section: bytes, case: str) -> list[bytes]:
    match = re.search(
        rb"^#{2,3} Case " + case.encode() + rb" .*?(?=^#{1,3} |\Z)",
        section,
        re.M | re.S,
    )
    if match is None:
        raise ValueError(f"Case {case} missing from frozen examples")
    return re.findall(rb"^```json\r?\n(.*?)^```", match.group(), re.M | re.S)


def _sort_projection(
    rows: list[_ScoredCategory], policy: N06RankingPolicy
) -> list[RuntimeRankedCategory]:
    candidates = [
        ResolvedCategoryCandidate(
            category_id=row.category_id,
            category_index=i,
            rule_ref=_sample_ref(row.category_id),
            score=row.score,
            model_position=i,
            reasoning="",
            evidence_refs=[],
        )
        for i, row in enumerate(rows)
    ]
    raw, _, _ = policy.rank(candidates, [r.category_id for r in rows])
    return raw


def _verify_a2(
    case: str, raw: bytes, expected: bytes, context: N06RequestContext
) -> None:
    actual: object
    if case == "02":
        projection = _MappingExample.model_validate_json(raw)
        ranking = _sort_projection(projection.active_ranking, context.ranking_policy)
        threshold = projection.empty_ranking_non_violation_threshold
        mapping = NonViolationMappingPolicy.model_validate(
            {
                **context.non_violation_mapping_policy.model_dump(),
                "margin": projection.margin,
                "empty_ranking_non_violation_threshold": threshold,
            }
        )
        verdict, _ = resolve_top_verdict(
            ranking, projection.non_violation_score, mapping
        )
        actual = {"top_verdict": verdict}
    elif case == "03":
        rows = TypeAdapter(list[_ScoredCategory]).validate_json(raw)
        actual = [
            {"rank": r.rank, "category_id": r.category_id}
            for r in _sort_projection(rows, context.ranking_policy)
        ]
    else:
        (outcome,) = validate_n06_output(raw, context)
        if case == "01":
            N06RawModelOutput.model_validate_json(raw)
            judgment = outcome.jev_judgment
            if judgment is None:
                raise ValueError(f"unexpected target failure: {outcome.error_codes}")
            actual = {
                "raw_ranking_category_indices": [
                    r.category_index for r in judgment.raw_ranking
                ],
                "active_ranking_category_indices": [
                    r.category_index for r in judgment.active_ranking
                ],
                "active_ranking_count": len(judgment.active_ranking),
            }
        else:
            TargetValidationOutcome.model_validate_json(expected)
            actual = outcome.model_dump(mode="json")
    if json.dumps(actual, sort_keys=True) != json.dumps(
        json.loads(expected), sort_keys=True
    ):
        raise ValueError(f"WP-A2 Case {case} result differs from expected: {actual}")


def _check_evidence_projection(
    category: dict[str, Any], row: _CategoryEvidenceDetails
) -> None:
    if (
        row.category_id,
        row.required_dependency_count,
        row.optional_dependency_count,
    ) != (
        category["category_id"],
        len(category["required_dependencies"]),
        len(category["optional_dependencies"]),
    ):
        raise ValueError("evidence result does not cover the supplied dependencies")
    failures = sorted(
        (required, dep["dependency_id"], dep["fact_status"])
        for required, key in (
            (True, "required_dependencies"),
            (False, "optional_dependencies"),
        )
        for dep in category[key]
        if dep["fact_status"] in {"MISSING", "ERROR", "UNAVAILABLE"}
    )
    refs = (
        row.missing_dependency_refs
        + row.error_dependency_refs
        + row.unavailable_dependency_refs
    )
    if failures != sorted(
        (ref.required, ref.dependency_id, ref.fact_status) for ref in refs
    ):
        raise ValueError("failure details do not describe input facts")


def _check_comparison_projection(
    source: dict[str, Any], result: dict[str, Any]
) -> None:
    ast = source["ast"]
    jev = source.get("jev")
    ranking = {c["category_id"]: c for c in jev["active_ranking"]} if jev else {}
    partitions: dict[RuleJudgmentStatus, list[str]] = {
        "TRIGGERED": ast["triggered_categories"],
        "NON_TRIGGERED": ast["non_triggered_categories"],
        "UNCERTAIN": ast["uncertain_categories"],
        "FAILED": ast["failed_categories"],
    }
    statuses = {
        category: status for status, values in partitions.items() for category in values
    }
    if jev:
        status = _status_for_categories(
            jev["top_verdict"],
            set(ranking),
            ast["aggregation_status"],
            set(partitions["TRIGGERED"]),
            bool(partitions["UNCERTAIN"]),
        )
    else:
        status = (
            "JEV_FAILED" if source["jev_status"] == "FAILED" else "JEV_MISSING_TARGET"
        )
    if result["comparison_status"] != status:
        raise ValueError("comparison priority does not match the input projections")
    if status not in _NO_METRICS:
        actual = TopKMetrics.from_categories(list(ranking), partitions["TRIGGERED"])
        expected = TopKMetrics.model_validate(result["topk_metrics"])
        # Both validated objects check ratios against their own denominators.
        if (
            actual.top1_hit,
            actual.intersection_count,
            actual.jev_active_count,
            actual.ast_triggered_count,
        ) != (
            expected.top1_hit,
            expected.intersection_count,
            expected.jev_active_count,
            expected.ast_triggered_count,
        ):
            raise ValueError("metrics disagree with the projected candidates")
    elif result["topk_metrics"] is not None:
        raise ValueError("failed target cannot have TopKMetrics")
    diffs = result["per_category_diffs"]
    if [d["category_id"] for d in diffs] != sorted(statuses.keys() | ranking.keys()):
        raise ValueError("category differences do not cover both sides")
    for diff in diffs:
        category = diff["category_id"]
        entry, ast_status = ranking.get(category), statuses.get(category)
        kind = category_diff_type(jev is not None, entry is not None, ast_status)
        if (diff["ast_status"], diff["diff_type"], diff["jev_rank"]) != (
            ast_status,
            kind,
            entry["rank"] if entry else None,
        ):
            raise ValueError("category difference does not match the input judgments")


def _verify_a345(case: str, raw: bytes, expected: bytes) -> None:
    # Compare the published projections; missing identities/provenance stay missing.
    source, data = json.loads(raw), json.loads(expected)
    if case in ("01", "02"):
        for category, item in zip(source["categories"], data["results"], strict=True):
            _check_evidence_projection(
                category, _CategoryEvidenceDetails.model_validate(item)
            )
    elif case == "03":
        summary = TargetASTSummary.model_validate_json(expected)
        judgments = sorted(source["rule_judgments"], key=lambda j: j["category_id"])
        if summary.category_statuses != {
            j["category_id"]: j["status"] for j in judgments
        } or (summary.rule_judgment_refs != [j["rule_trace_ref"] for j in judgments]):
            raise ValueError("AST summary lost a category or trace")
    elif case == "06":
        for target, result in zip(source["targets"], data["results"], strict=True):
            if target["target_instance_id"] != result["target_instance_id"]:
                raise ValueError("comparison target identity mismatch")
            _check_comparison_projection(target, result)
    else:
        _check_comparison_projection(source, data)


def verify_fixtures(contracts_dir: Path) -> int:
    cases = (
        (
            "wp_a2",
            "WP_A2_Ranking_Schema_V1.1.md",
            b"## 9. Minimal Positive / Negative JSON Examples",
            5,
        ),
        (
            "wp_a345",
            "WP-A3_A4_A5_Evidence_Comparison_Closure_V1.1.md",
            b"# 5. Minimal Positive / Negative JSON Examples",
            6,
        ),
    )
    context = sample_context()
    count = 0
    for suite, filename, heading, total in cases:
        source = (contracts_dir / "frozen" / filename).read_bytes()
        section = source.split(heading, 1)[1]
        for number in range(1, total + 1):
            case = f"{number:02}"
            directory = contracts_dir / "fixtures" / suite
            raw = (directory / f"case_{case}.input.json").read_bytes()
            expected = (directory / f"case_{case}.expected.json").read_bytes()
            blocks = _blocks(section, case)
            if suite == "wp_a345":
                if blocks != [raw, expected]:
                    raise ValueError(
                        f"{suite} Case {case} differs from the frozen JSON blocks"
                    )
                _verify_a345(case, raw, expected)
            else:
                if case in ("01", "04", "05") and blocks != [raw]:
                    raise ValueError(
                        f"{suite} Case {case} differs from the frozen wire JSON"
                    )
                _verify_a2(case, raw, expected, context)
            count += 1
    return count
