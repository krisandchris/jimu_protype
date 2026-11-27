"""WP-A2 resolved catalogs, evidence, versioned policies and JEV judgments."""

from collections.abc import Collection, Iterable
from decimal import Decimal
from operator import attrgetter
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from .base import (
    AssetVersionRef,
    NonNegativeInt,
    StrictModel,
    TargetKind,
    require_unique,
)
from .wp_a2_wire import FiniteUnitFloat

TopVerdictValue: TypeAlias = Literal["VIOLATION", "NON_VIOLATION", "UNCERTAIN"]
TieBreakRuleValue: TypeAlias = Literal["score_desc_category_id_asc"]
FiniteFloat: TypeAlias = Annotated[StrictFloat, Field(allow_inf_nan=False)]


class BundleCategoryEntry(StrictModel):
    category_index: NonNegativeInt
    category_id: StrictStr
    rule_ref: AssetVersionRef


class BundleCategoryCatalog(StrictModel):
    entries: list[BundleCategoryEntry] = Field(min_length=1)
    catalog_digest: StrictStr

    @model_validator(mode="after")
    def validate_uniqueness(self) -> Self:
        require_unique((e.category_index for e in self.entries), "category_index")
        require_unique((e.category_id for e in self.entries), "category_id")
        require_unique((e.rule_ref.asset_id for e in self.entries), "rule identity")
        return self


class TargetReferenceEntry(StrictModel):
    target_ref_index: NonNegativeInt
    target_instance_id: StrictStr
    target_kind: TargetKind


class RuntimeTargetReferenceCatalog(StrictModel):
    entries: list[TargetReferenceEntry] = Field(min_length=1)
    image_execution_id: StrictStr
    catalog_digest: StrictStr

    @model_validator(mode="after")
    def validate_uniqueness(self) -> Self:
        require_unique((e.target_ref_index for e in self.entries), "target_ref_index")
        require_unique(
            (e.target_instance_id for e in self.entries), "target_instance_id"
        )
        return self


class VisualRegionRef(StrictModel):
    source: Literal["visual"]
    image_execution_id: StrictStr
    evidence_id: StrictStr
    bbox_xyxy: list[FiniteFloat] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_bbox(self) -> Self:
        x1, y1, x2, y2 = self.bbox_xyxy
        if x1 >= x2 or y1 >= y2:
            raise ValueError("bbox_xyxy requires x1 < x2 and y1 < y2")
        return self


class ObservationFactEvidenceRef(StrictModel):
    source: Literal["observation"]
    image_execution_id: StrictStr
    fact_id: StrictStr
    target_instance_id: StrictStr
    question_ref: AssetVersionRef
    field_key: StrictStr


class ToolFactEvidenceRef(StrictModel):
    source: Literal["tool"]
    image_execution_id: StrictStr
    fact_id: StrictStr
    target_instance_id: StrictStr | None
    scope: Literal["TARGET", "GLOBAL"]
    tool_ref: AssetVersionRef
    step_key: StrictStr
    fact_key: StrictStr

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if (self.scope == "GLOBAL") != (self.target_instance_id is None):
            raise ValueError("tool fact target identity does not match scope")
        return self


EvidenceRef: TypeAlias = Annotated[
    VisualRegionRef | ObservationFactEvidenceRef | ToolFactEvidenceRef,
    Field(discriminator="source"),
]


def _evidence_by_identity(
    refs: Iterable[EvidenceRef],
) -> dict[tuple[str, str], EvidenceRef]:
    by_id: dict[tuple[str, str], EvidenceRef] = {}
    for ref in refs:
        key = (
            ref.source,
            ref.evidence_id if isinstance(ref, VisualRegionRef) else ref.fact_id,
        )
        if key in by_id and by_id[key] != ref:
            raise ValueError("one evidence identity has conflicting payloads")
        by_id[key] = ref
    return by_id


class _PositionedCategory(StrictModel):
    # The frozen field model_position coexists with Pydantic 2.7's model_* API.
    model_config = ConfigDict(protected_namespaces=("model_validate", "model_dump"))

    category_index: NonNegativeInt
    category_id: StrictStr
    score: FiniteUnitFloat
    model_position: NonNegativeInt


class ResolvedCategoryCandidate(_PositionedCategory):
    rule_ref: AssetVersionRef
    reasoning: StrictStr
    evidence_refs: list[EvidenceRef]

    @property
    def sort_key(self) -> tuple[float, str]:
        return -self.score, self.category_id


class RuntimeRankedCategory(ResolvedCategoryCandidate):
    rank: StrictInt = Field(ge=1)


class IsolatedCategoryRecord(_PositionedCategory):
    reason_code: Literal["category_out_of_active_scope"]


def _mapping_verdict(
    top1: float | None, non_violation: float, margin: float, threshold: float
) -> TopVerdictValue:
    v = Decimal(str(non_violation))
    if top1 is None:
        return "NON_VIOLATION" if v >= Decimal(str(threshold)) else "UNCERTAIN"
    c, m = Decimal(str(top1)), Decimal(str(margin))
    if v == c and m == 0:
        return "UNCERTAIN"
    if v >= c + m:
        return "NON_VIOLATION"
    if c >= v + m:
        return "VIOLATION"
    return "UNCERTAIN"


class NonViolationMappingAudit(StrictModel):
    top1_score: FiniteUnitFloat | None
    non_violation_score: FiniteUnitFloat
    margin: FiniteUnitFloat
    empty_ranking_non_violation_threshold: FiniteUnitFloat
    active_ranking_empty: StrictBool
    resolved_verdict: TopVerdictValue

    @model_validator(mode="after")
    def validate_mapping(self) -> Self:
        if self.active_ranking_empty != (self.top1_score is None):
            raise ValueError("top1_score presence must match active ranking")
        expected = _mapping_verdict(
            self.top1_score,
            self.non_violation_score,
            self.margin,
            self.empty_ranking_non_violation_threshold,
        )
        if self.resolved_verdict != expected:
            raise ValueError("verdict contradicts decimal mapping policy")
        return self


def aggregate_evidence(ranking: list[RuntimeRankedCategory]) -> list[EvidenceRef]:
    return list(
        _evidence_by_identity(
            ref for row in ranking for ref in row.evidence_refs
        ).values()
    )


class TargetJEVJudgment(StrictModel):
    image_execution_id: StrictStr
    target_instance_id: StrictStr
    target_kind: TargetKind
    bundle_digest: StrictStr
    category_catalog_digest: StrictStr
    ranking_policy_ref: AssetVersionRef
    non_violation_mapping_policy_ref: AssetVersionRef
    raw_ranking: list[RuntimeRankedCategory]
    active_ranking: list[RuntimeRankedCategory]
    isolated_categories: list[IsolatedCategoryRecord]
    non_violation_score: FiniteUnitFloat
    top_verdict: TopVerdictValue
    mapping_audit: NonViolationMappingAudit
    evidence_refs: list[EvidenceRef]

    @model_validator(mode="after")
    def validate_rankings(self) -> Self:
        candidates: list[RuntimeRankedCategory | IsolatedCategoryRecord] = [
            *self.raw_ranking,
            *self.isolated_categories,
        ]
        require_unique((c.category_index for c in candidates), "category_index")
        require_unique((c.category_id for c in candidates), "category_id")
        require_unique((c.model_position for c in candidates), "model_position")
        require_unique((c.rule_ref.asset_id for c in self.raw_ranking), "rule identity")
        if self.raw_ranking != sorted(self.raw_ranking, key=attrgetter("sort_key")):
            raise ValueError("raw ranking is not in canonical order")
        if any(c.rank != i for i, c in enumerate(self.raw_ranking, 1)):
            raise ValueError("rank must be consecutive and start at one")
        if self.active_ranking != self.raw_ranking[: len(self.active_ranking)]:
            raise ValueError("active ranking must be a prefix of raw ranking")
        top1 = self.active_ranking[0].score if self.active_ranking else None
        if (top1, self.non_violation_score, self.top_verdict) != (
            self.mapping_audit.top1_score,
            self.mapping_audit.non_violation_score,
            self.mapping_audit.resolved_verdict,
        ):
            raise ValueError("mapping audit does not match judgment")
        aggregate = _evidence_by_identity(
            ref for c in self.raw_ranking for ref in c.evidence_refs
        )
        actual = _evidence_by_identity(self.evidence_refs)
        if actual != aggregate or len(actual) != len(self.evidence_refs):
            raise ValueError(
                "judgment must retain the unique evidence of all raw candidates"
            )
        for ref in self.evidence_refs:
            if ref.image_execution_id != self.image_execution_id:
                raise ValueError("evidence belongs to another image")
            if not isinstance(ref, VisualRegionRef) and ref.target_instance_id not in (
                None,
                self.target_instance_id,
            ):
                raise ValueError("evidence belongs to another target")
        return self


class _VersionedPolicy(StrictModel):
    policy_id: StrictStr = Field(min_length=1)
    version: StrictStr = Field(min_length=1)
    content_hash: StrictStr = Field(min_length=1)

    @property
    def asset_ref(self) -> AssetVersionRef:
        return AssetVersionRef(
            asset_id=self.policy_id,
            version=self.version,
            content_hash=self.content_hash,
        )


class N06RankingPolicy(_VersionedPolicy):
    top_k: StrictInt = Field(gt=0)
    min_candidate_score: FiniteUnitFloat
    tie_break_rule: TieBreakRuleValue
    policy_description: StrictStr

    def rank(
        self, candidates: list[ResolvedCategoryCandidate], active_ids: Collection[str]
    ) -> tuple[
        list[RuntimeRankedCategory],
        list[RuntimeRankedCategory],
        list[IsolatedCategoryRecord],
    ]:
        require_unique((c.category_index for c in candidates), "category_index")
        require_unique((c.category_id for c in candidates), "category_id")
        eligible = sorted(
            (c for c in candidates if c.category_id in active_ids),
            key=attrgetter("sort_key"),
        )
        raw = [
            RuntimeRankedCategory(rank=i, **c.model_dump())
            for i, c in enumerate(eligible, 1)
        ]
        active = [c for c in raw if c.score >= self.min_candidate_score][: self.top_k]
        isolated = [
            IsolatedCategoryRecord(
                category_index=c.category_index,
                category_id=c.category_id,
                score=c.score,
                model_position=c.model_position,
                reason_code="category_out_of_active_scope",
            )
            for c in candidates
            if c.category_id not in active_ids
        ]
        return raw, active, isolated


class NonViolationMappingPolicy(_VersionedPolicy):
    mode: Literal["score_comparison"]
    margin: FiniteUnitFloat
    empty_ranking_non_violation_threshold: FiniteUnitFloat
    policy_description: StrictStr


def resolve_top_verdict(
    active_ranking: list[RuntimeRankedCategory],
    non_violation_score: float,
    policy: NonViolationMappingPolicy,
) -> tuple[TopVerdictValue, NonViolationMappingAudit]:
    top1 = active_ranking[0].score if active_ranking else None
    verdict = _mapping_verdict(
        top1,
        non_violation_score,
        policy.margin,
        policy.empty_ranking_non_violation_threshold,
    )
    return verdict, NonViolationMappingAudit(
        top1_score=top1,
        non_violation_score=non_violation_score,
        margin=policy.margin,
        empty_ranking_non_violation_threshold=policy.empty_ranking_non_violation_threshold,
        active_ranking_empty=not active_ranking,
        resolved_verdict=verdict,
    )
