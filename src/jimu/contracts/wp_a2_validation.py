"""Bind the two-stage N06 wire response to an explicit request snapshot."""

from collections import defaultdict
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, ValidationError, model_validator

from .base import AssetVersionRef, NonNegativeInt, StrictModel, require_unique
from .evaluation import RuleTargetEvaluation
from .wp_a2_runtime import (
    BundleCategoryCatalog,
    BundleCategoryEntry,
    EvidenceRef,
    N06RankingPolicy,
    NonViolationMappingPolicy,
    ObservationFactEvidenceRef,
    ResolvedCategoryCandidate,
    RuntimeTargetReferenceCatalog,
    TargetJEVJudgment,
    TargetReferenceEntry,
    ToolFactEvidenceRef,
    aggregate_evidence,
    resolve_top_verdict,
)
from .wp_a2_wire import (
    N06ErrorCode,
    N06RawEnvelope,
    RawCategoryEvidence,
    RawTargetJEVResult,
)


class IndexedEvidence(StrictModel):
    evidence_index: NonNegativeInt
    evidence_ref: EvidenceRef


class N06RequestContext(StrictModel):
    """Inputs supplied by the caller for validation, never fields on the LLM wire."""

    bundle_digest: str
    category_catalog: BundleCategoryCatalog
    target_catalog: RuntimeTargetReferenceCatalog
    active_evaluations: list[RuleTargetEvaluation]
    evidence_catalog: list[IndexedEvidence]
    question_refs: list[AssetVersionRef]
    tool_refs: list[AssetVersionRef]
    ranking_policy: N06RankingPolicy
    non_violation_mapping_policy: NonViolationMappingPolicy

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        rules = {e.category_id: e.rule_ref for e in self.category_catalog.entries}
        kinds = {
            e.target_instance_id: e.target_kind for e in self.target_catalog.entries
        }
        require_unique(
            ((e.target_instance_id, e.category_id) for e in self.active_evaluations),
            "active evaluation",
        )
        for e in self.active_evaluations:
            if (
                rules.get(e.category_id) != e.rule_ref
                or kinds.get(e.target_instance_id) != e.target_kind
            ):
                raise ValueError("active evaluation does not match the catalog locks")
        require_unique((r.asset_id for r in self.question_refs), "question lock")
        require_unique((r.asset_id for r in self.tool_refs), "tool lock")
        return self


def _parse_error_code(value: object) -> object:
    # Support an already-decoded JSON string in Pydantic's strict Python mode.
    return N06ErrorCode(value) if type(value) is str else value


class TargetValidationOutcome(StrictModel):
    target_ref_index: NonNegativeInt
    status: Literal["SUCCEEDED", "JEV_FAILED", "JEV_MISSING_TARGET"]
    jev_judgment: TargetJEVJudgment | None
    error_codes: list[Annotated[N06ErrorCode, BeforeValidator(_parse_error_code)]]

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "SUCCEEDED":
            if self.jev_judgment is None or self.error_codes:
                raise ValueError("successful target requires a judgment and no errors")
            return self
        if self.jev_judgment is not None or not self.error_codes:
            raise ValueError("failed or missing target requires errors and no judgment")
        require_unique(self.error_codes, "N06 error code")
        if self.status == "JEV_MISSING_TARGET":
            if self.error_codes != [N06ErrorCode.N06_TARGET_MISSING]:
                raise ValueError("a missing target must carry only the missing code")
        elif any(
            code
            in {
                N06ErrorCode.N06_TARGET_MISSING,
                N06ErrorCode.N06_CATEGORY_OUT_OF_ACTIVE_SCOPE,
                N06ErrorCode.N06_JSON_PARSE_FAILED,
                N06ErrorCode.N06_OUTPUT_TRUNCATED,
            }
            for code in self.error_codes
        ):
            raise ValueError("error code does not describe a target-level failure")
        return self


class N06ImageFailure(ValueError):
    failure_code = "N06_FAILED"

    def __init__(self, error_code: N06ErrorCode) -> None:
        super().__init__(error_code.value)
        self.error_code = error_code


def _schema_errors(exc: ValidationError) -> list[N06ErrorCode]:
    codes = []
    for err in exc.errors():
        loc, value = err["loc"], err["input"]
        if (
            ("score" in loc or "non_violation_score" in loc)
            and type(value) in (int, float)
            and not 0 <= value <= 1
        ):
            codes.append(N06ErrorCode.N06_SCORE_OUT_OF_RANGE)
        elif "category_index" in loc:
            codes.append(N06ErrorCode.N06_CATEGORY_INDEX_INVALID)
        elif "evidence" in loc and err["type"] != "extra_forbidden":
            codes.append(N06ErrorCode.N06_EVIDENCE_REFERENCE_INVALID)
        else:
            codes.append(N06ErrorCode.N06_SCHEMA_VALIDATION_FAILED)
    return codes


def _category_errors(
    item: dict[str, object], categories: dict[int, BundleCategoryEntry]
) -> list[N06ErrorCode]:
    # This check is independent of score validation. §9 Case 04 records both causes.
    rows = item.get("ranked_categories")
    if not isinstance(rows, list):
        return []
    indices = [
        r["category_index"]
        for r in rows
        if isinstance(r, dict) and type(r.get("category_index")) is int
    ]
    codes = []
    if any(i not in categories for i in indices):
        codes.append(N06ErrorCode.N06_CATEGORY_INDEX_INVALID)
    if len(indices) != len(set(indices)):
        codes.append(N06ErrorCode.N06_DUPLICATE_CATEGORY)
    return codes


def _resolve_evidence(
    wire: RawCategoryEvidence,
    catalog: dict[tuple[str, int], list[EvidenceRef]],
    target_id: str,
    context: N06RequestContext,
) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for source, indices in (
        ("visual", wire.visual_evidence_indices),
        ("observation", wire.observation_evidence_indices),
        ("tool", wire.tool_evidence_indices),
    ):
        require_unique(indices, "evidence index")
        for index in indices:
            matches = catalog.get((source, index), [])
            if len(matches) != 1:
                raise ValueError("evidence index does not resolve uniquely")
            ref = matches[0]
            if ref.image_execution_id != context.target_catalog.image_execution_id:
                raise ValueError("evidence image mismatch")
            if isinstance(ref, ObservationFactEvidenceRef):
                if (
                    ref.target_instance_id != target_id
                    or ref.question_ref not in context.question_refs
                ):
                    raise ValueError("observation target or producer version mismatch")
            if isinstance(ref, ToolFactEvidenceRef):
                if (
                    ref.target_instance_id not in (None, target_id)
                    or ref.tool_ref not in context.tool_refs
                ):
                    raise ValueError("tool target or producer version mismatch")
            refs.append(ref)
    return refs


def _outcome(
    item: dict[str, object],
    target: TargetReferenceEntry,
    context: N06RequestContext,
    categories: dict[int, BundleCategoryEntry],
    evidence: dict[tuple[str, int], list[EvidenceRef]],
) -> TargetValidationOutcome:
    codes: list[N06ErrorCode] = []
    try:
        wire = RawTargetJEVResult.model_validate(item)
    except ValidationError as exc:
        codes.extend(_schema_errors(exc))
        wire = None
    codes.extend(_category_errors(item, categories))
    candidates = []
    if wire is not None and not codes:
        for position, entry in enumerate(wire.ranked_categories):
            category = categories[entry.category_index]
            try:
                refs = _resolve_evidence(
                    entry.evidence, evidence, target.target_instance_id, context
                )
            except ValueError:
                codes.append(N06ErrorCode.N06_EVIDENCE_REFERENCE_INVALID)
                break
            candidates.append(
                ResolvedCategoryCandidate(
                    **category.model_dump(),
                    score=entry.score,
                    model_position=position,
                    reasoning=entry.reasoning,
                    evidence_refs=refs,
                )
            )
    if wire is None or codes:
        return TargetValidationOutcome(
            target_ref_index=target.target_ref_index,
            status="JEV_FAILED",
            jev_judgment=None,
            error_codes=list(dict.fromkeys(codes)),
        )

    active_ids = {
        e.category_id
        for e in context.active_evaluations
        if e.target_instance_id == target.target_instance_id
    }
    policy = context.ranking_policy
    mapping = context.non_violation_mapping_policy
    raw, active, isolated = policy.rank(candidates, active_ids)
    verdict, audit = resolve_top_verdict(active, wire.non_violation_score, mapping)
    try:
        aggregate = aggregate_evidence(raw)
    except ValueError:
        return TargetValidationOutcome(
            target_ref_index=target.target_ref_index,
            status="JEV_FAILED",
            jev_judgment=None,
            error_codes=[N06ErrorCode.N06_EVIDENCE_REFERENCE_INVALID],
        )
    judgment = TargetJEVJudgment(
        image_execution_id=context.target_catalog.image_execution_id,
        target_instance_id=target.target_instance_id,
        target_kind=target.target_kind,
        bundle_digest=context.bundle_digest,
        category_catalog_digest=context.category_catalog.catalog_digest,
        ranking_policy_ref=policy.asset_ref,
        non_violation_mapping_policy_ref=mapping.asset_ref,
        raw_ranking=raw,
        active_ranking=active,
        isolated_categories=isolated,
        non_violation_score=wire.non_violation_score,
        top_verdict=verdict,
        mapping_audit=audit,
        evidence_refs=aggregate,
    )
    return TargetValidationOutcome(
        target_ref_index=target.target_ref_index,
        status="SUCCEEDED",
        jev_judgment=judgment,
        error_codes=[],
    )


def validate_n06_output(
    raw_text: str | bytes,
    context: N06RequestContext,
    *,
    output_truncated: bool = False,
) -> list[TargetValidationOutcome]:
    if output_truncated:
        raise N06ImageFailure(N06ErrorCode.N06_OUTPUT_TRUNCATED)
    try:
        envelope = N06RawEnvelope.model_validate_json(raw_text)
    except ValidationError as exc:
        code = (
            N06ErrorCode.N06_JSON_PARSE_FAILED
            if exc.errors()[0]["type"] == "json_invalid"
            else N06ErrorCode.N06_SCHEMA_VALIDATION_FAILED
        )
        raise N06ImageFailure(code) from exc
    expected = {t.target_ref_index: t for t in context.target_catalog.entries}
    by_target: dict[int, list[dict[str, object]]] = defaultdict(list)
    for item in envelope.results:
        index = item.get("target_ref_index")
        if type(index) is not int or index not in expected:
            raise N06ImageFailure(N06ErrorCode.N06_TARGET_REFERENCE_INVALID)
        by_target[index].append(item)

    categories = {c.category_index: c for c in context.category_catalog.entries}
    evidence: dict[tuple[str, int], list[EvidenceRef]] = defaultdict(list)
    for entry in context.evidence_catalog:
        evidence[entry.evidence_ref.source, entry.evidence_index].append(
            entry.evidence_ref
        )
    outcomes = []
    for index, target in sorted(expected.items()):
        rows = by_target[index]
        if len(rows) == 1:
            outcomes.append(_outcome(rows[0], target, context, categories, evidence))
        else:
            outcomes.append(
                TargetValidationOutcome(
                    target_ref_index=index,
                    jev_judgment=None,
                    status="JEV_FAILED" if rows else "JEV_MISSING_TARGET",
                    error_codes=[
                        N06ErrorCode.N06_DUPLICATE_TARGET
                        if rows
                        else N06ErrorCode.N06_TARGET_MISSING
                    ],
                )
            )
    return outcomes
