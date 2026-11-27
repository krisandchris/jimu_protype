"""WP-A5 target comparison, protocol failures and audit provenance."""

from typing import Literal, Self, TypeAlias

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from .base import FiniteScore, NonNegativeInt, StrictModel, require_unique
from .wp_a2_runtime import TargetJEVJudgment, TopVerdictValue
from .wp_a3_evidence import CategoryEvidenceCompleteness, EvidenceCompletenessStatus
from .wp_a4_ast import ASTAggregationStatus, RuleJudgmentStatus, TargetASTSummary

JEVResultAvailability: TypeAlias = Literal["AVAILABLE", "MISSING", "FAILED"]
ASTResultAvailability: TypeAlias = Literal["AVAILABLE", "MISSING"]
CategoryDiffType: TypeAlias = Literal[
    "AGREED_TRIGGERED",
    "JEV_CANDIDATE_AST_NON_TRIGGERED",
    "JEV_CANDIDATE_AST_UNCERTAIN",
    "AST_TRIGGERED_JEV_MISSED",
    "AST_NON_TRIGGERED_NOT_RANKED",
    "AST_UNCERTAIN_NOT_RANKED",
    "AST_FAILED",
    "JEV_ONLY",
    "AST_ONLY_JEV_UNAVAILABLE",
    "NO_ACTIVE_JUDGMENT",
]
ComparisonStatus: TypeAlias = Literal[
    "AGREE_TOP1",
    "AGREE_NON_VIOLATION",
    "AGREE_TOPK_RANK_DIFF",
    "CATEGORY_CONFLICT",
    "EXISTENCE_CONFLICT",
    "AST_PARTIAL_FAILED",
    "AST_FAILED",
    "JEV_MISSING_TARGET",
    "JEV_FAILED",
    "AST_MISSING",
    "BOTH_MISSING",
    "JEV_UNCERTAIN",
    "AST_UNCERTAIN",
    "BOTH_UNCERTAIN",
    "CATEGORY_SET_PARTIAL_MATCH",
]

_NO_METRICS = {
    "BOTH_MISSING",
    "AST_MISSING",
    "JEV_MISSING_TARGET",
    "JEV_FAILED",
    "AST_FAILED",
    "AST_PARTIAL_FAILED",
}


class ComparatorInput(StrictModel):
    image_execution_id: StrictStr
    target_instance_id: StrictStr
    bundle_digest: StrictStr
    jev_judgment_ref: StrictStr | None
    ast_summary_ref: StrictStr | None
    evidence_completeness_refs: list[StrictStr]


class PerCategoryDiff(StrictModel):
    category_id: StrictStr
    jev_rank: StrictInt | None = Field(default=None, ge=1)
    jev_score: FiniteScore | None = None
    ast_status: RuleJudgmentStatus | None
    evidence_completeness_status: EvidenceCompletenessStatus | None
    rule_judgment_ref: StrictStr | None
    diff_type: CategoryDiffType


class TopKMetrics(StrictModel):
    top1_hit: StrictBool | None
    topk_hit: StrictBool | None
    topk_coverage_ratio: FiniteScore | None = None
    topk_precision_ratio: FiniteScore | None = None
    ast_triggered_count: NonNegativeInt
    jev_active_count: NonNegativeInt
    intersection_count: NonNegativeInt

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        a, j, n = (
            self.ast_triggered_count,
            self.jev_active_count,
            self.intersection_count,
        )
        if n > min(a, j):
            raise ValueError("intersection exceeds an input set")
        if (self.top1_hit is None) != (a == 0) or self.topk_hit != (
            n == a if a else None
        ):
            raise ValueError("hit flags inconsistent with AST coverage")
        if self.top1_hit and not n:
            raise ValueError("a top-1 hit requires an intersection")
        if self.top1_hit is False and j and n == j:
            raise ValueError("top-1 must hit when all ranked categories are triggered")
        for denominator, ratio in (
            (a, self.topk_coverage_ratio),
            (j, self.topk_precision_ratio),
        ):
            if denominator == 0:
                if ratio is not None:
                    raise ValueError("a ratio with an empty denominator must be null")
            else:
                expected = n / denominator
                # §5 Case 04 publishes one third as 0.3333333333.
                if ratio not in (expected, round(expected, 10)):
                    raise ValueError("ratio inconsistent with set counts")
        return self

    @classmethod
    def from_categories(cls, active: list[str], triggered: list[str]) -> Self:
        require_unique(active, "active category")
        require_unique(triggered, "triggered category")
        a, j = set(triggered), set(active)
        n = len(j & a)
        return cls(
            top1_hit=(bool(active) and active[0] in a) if a else None,
            topk_hit=a <= j if a else None,
            topk_coverage_ratio=n / len(a) if a else None,
            topk_precision_ratio=n / len(j) if j else None,
            ast_triggered_count=len(a),
            jev_active_count=len(j),
            intersection_count=n,
        )


class ComparisonProvenance(StrictModel):
    image_execution_id: StrictStr
    target_instance_id: StrictStr
    bundle_digest: StrictStr
    category_catalog_digest: StrictStr
    planner_version: StrictStr
    target_planning_snapshot_hash: StrictStr
    jev_judgment_ref: StrictStr | None
    ast_summary_ref: StrictStr | None
    evidence_completeness_refs: list[StrictStr]
    n04_final_attempt_id: StrictStr | None
    n05_execution_id: StrictStr | None
    compiled_tool_plan_digest: StrictStr | None
    runtime_tool_plan_hash: StrictStr | None


def available_comparison_status(
    jev: TargetJEVJudgment, ast: TargetASTSummary
) -> ComparisonStatus:
    """Check the two resolved identities before evaluating P3–P7."""
    if (jev.image_execution_id, jev.target_instance_id) != (
        ast.image_execution_id,
        ast.target_instance_id,
    ):
        raise ValueError("comparison image or target identity mismatch")
    return _status_for_categories(
        jev.top_verdict,
        {row.category_id for row in jev.active_ranking},
        ast.aggregation_status,
        set(ast.triggered_categories),
        bool(ast.uncertain_categories),
    )


def _status_for_categories(
    verdict: TopVerdictValue,
    ranked: set[str],
    aggregation: ASTAggregationStatus,
    triggered: set[str],
    has_uncertain: bool,
) -> ComparisonStatus:
    if (verdict == "NON_VIOLATION" and ranked) or (
        verdict == "VIOLATION" and not ranked
    ):
        return "JEV_FAILED"
    if aggregation == "ALL_FAILED":
        return "AST_FAILED"
    if aggregation == "PARTIAL_FAILED":
        return "AST_PARTIAL_FAILED"
    ast_uncertain = not triggered and has_uncertain
    if verdict == "UNCERTAIN":
        return "BOTH_UNCERTAIN" if ast_uncertain else "JEV_UNCERTAIN"
    if ast_uncertain:
        return "AST_UNCERTAIN"
    if not triggered or not ranked:
        return (
            "AGREE_NON_VIOLATION"
            if not triggered and not ranked
            else "EXISTENCE_CONFLICT"
        )
    if not ranked & triggered:
        return "CATEGORY_CONFLICT"
    if not triggered <= ranked:
        return "CATEGORY_SET_PARTIAL_MATCH"
    return "AGREE_TOP1" if triggered == ranked else "AGREE_TOPK_RANK_DIFF"


def category_diff_type(
    jev_available: bool, ranked: bool, ast: RuleJudgmentStatus | None
) -> CategoryDiffType:
    if ranked and not jev_available:
        raise ValueError("unavailable JEV cannot supply a ranked category")
    if ast == "FAILED":
        return "AST_FAILED"
    if ast is None:
        return "JEV_ONLY" if ranked else "NO_ACTIVE_JUDGMENT"
    if not jev_available:
        return "AST_ONLY_JEV_UNAVAILABLE"
    if ast == "TRIGGERED":
        return "AGREED_TRIGGERED" if ranked else "AST_TRIGGERED_JEV_MISSED"
    if ast == "NON_TRIGGERED":
        return (
            "JEV_CANDIDATE_AST_NON_TRIGGERED"
            if ranked
            else "AST_NON_TRIGGERED_NOT_RANKED"
        )
    return "JEV_CANDIDATE_AST_UNCERTAIN" if ranked else "AST_UNCERTAIN_NOT_RANKED"


class TargetComparisonResult(StrictModel):
    image_execution_id: StrictStr
    target_instance_id: StrictStr
    comparison_status: ComparisonStatus
    ast_summary: TargetASTSummary | None
    jev_judgment: TargetJEVJudgment | None
    evidence_completeness_by_category: list[CategoryEvidenceCompleteness]
    topk_metrics: TopKMetrics | None
    per_category_diffs: list[PerCategoryDiff]
    provenance: ComparisonProvenance

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        ast, jev, provenance = self.ast_summary, self.jev_judgment, self.provenance
        for obj in [provenance, ast, jev, *self.evidence_completeness_by_category]:
            if obj is not None and (obj.image_execution_id, obj.target_instance_id) != (
                self.image_execution_id,
                self.target_instance_id,
            ):
                raise ValueError("comparison image or target identity mismatch")
        if jev and (jev.bundle_digest, jev.category_catalog_digest) != (
            provenance.bundle_digest,
            provenance.category_catalog_digest,
        ):
            raise ValueError("comparison bundle or category catalog mismatch")
        if (ast and provenance.ast_summary_ref is None) or (
            jev and provenance.jev_judgment_ref is None
        ):
            raise ValueError(
                "available judgment or summary is missing its provenance reference"
            )
        status = self.comparison_status
        if (ast is None) != (status in {"BOTH_MISSING", "AST_MISSING"}):
            raise ValueError("AST summary presence inconsistent with status")
        if status in {"BOTH_MISSING", "JEV_MISSING_TARGET", "JEV_FAILED"}:
            if jev is not None:
                raise ValueError("unavailable JEV cannot have an active judgment")
        elif status != "AST_MISSING" and jev is None:
            raise ValueError("missing JEV judgment for comparison status")
        if jev and ast and available_comparison_status(jev, ast) != status:
            raise ValueError("comparison status contradicts comparator priority")
        if (self.topk_metrics is None) != (status in _NO_METRICS):
            raise ValueError("TopKMetrics presence contradicts comparator priority")

        evidence_ids = [e.category_id for e in self.evidence_completeness_by_category]
        require_unique(evidence_ids, "category evidence")
        if evidence_ids != sorted(evidence_ids) or len(evidence_ids) != len(
            provenance.evidence_completeness_refs
        ):
            raise ValueError("evidence order or provenance reference count mismatch")
        require_unique(
            provenance.evidence_completeness_refs, "evidence completeness reference"
        )
        evidence = {e.category_id: e for e in self.evidence_completeness_by_category}
        if jev:
            for candidate in jev.raw_ranking:
                detail = evidence.get(candidate.category_id)
                if detail is not None and detail.rule_ref != candidate.rule_ref:
                    raise ValueError("JEV and evidence rule versions disagree")
        ranking = {row.category_id: row for row in jev.active_ranking} if jev else {}
        statuses = ast.category_statuses if ast else {}
        if [d.category_id for d in self.per_category_diffs] != sorted(
            ranking.keys() | statuses.keys()
        ):
            raise ValueError(
                "diffs must cover the sorted union of JEV and AST categories"
            )
        traces = (
            dict(zip(sorted(statuses), ast.rule_judgment_refs, strict=True))
            if ast
            else {}
        )
        for diff in self.per_category_diffs:
            c = diff.category_id
            row, ast_status, item = ranking.get(c), statuses.get(c), evidence.get(c)
            if diff.diff_type != category_diff_type(
                jev is not None, row is not None, ast_status
            ):
                raise ValueError(
                    "category diff type inconsistent with available judgments"
                )
            if (diff.ast_status, diff.jev_rank, diff.jev_score) != (
                ast_status,
                row.rank if row else None,
                row.score if row else None,
            ):
                raise ValueError("category diff does not match its source judgments")
            if diff.rule_judgment_ref != traces.get(
                c
            ) or diff.evidence_completeness_status != (item.status if item else None):
                raise ValueError("category diff trace or evidence mismatch")
        if self.topk_metrics is not None and ast is not None:
            metrics = TopKMetrics.from_categories(
                list(ranking), ast.triggered_categories
            )
            actual = self.topk_metrics
            if (
                actual.top1_hit,
                actual.ast_triggered_count,
                actual.jev_active_count,
                actual.intersection_count,
            ) != (
                metrics.top1_hit,
                metrics.ast_triggered_count,
                metrics.jev_active_count,
                metrics.intersection_count,
            ):
                raise ValueError("metrics do not describe the resolved judgments")
        return self


class ComparisonProtocolFailure(StrictModel):
    image_execution_id: StrictStr
    target_instance_id: StrictStr
    error_code: Literal[
        "IMAGE_EXECUTION_MISMATCH",
        "TARGET_IDENTITY_MISMATCH",
        "BUNDLE_DIGEST_MISMATCH",
        "CATEGORY_CATALOG_MISMATCH",
        "RULE_VERSION_MISMATCH",
        "PROVENANCE_MISMATCH",
    ]
    error_message: StrictStr
    source_refs: list[StrictStr]
