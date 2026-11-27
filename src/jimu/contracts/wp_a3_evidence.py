"""WP-A3 compiled dependencies and category-level evidence completeness."""

from typing import Literal, Self, TypeAlias

from pydantic import StrictBool, StrictStr, model_validator

from .base import AssetVersionRef, NonNegativeInt, StrictModel, require_unique

FactAvailability: TypeAlias = Literal[
    "KNOWN", "UNKNOWN", "NULL", "NOT_APPLICABLE", "MISSING", "ERROR", "UNAVAILABLE"
]
EvidenceCompletenessStatus: TypeAlias = Literal["COMPLETE", "PARTIAL", "UNAVAILABLE"]


class CompiledRuleFieldDependency(StrictModel):
    rule_ref: AssetVersionRef
    question_ref: AssetVersionRef
    field_key: StrictStr
    target_scope: Literal["OBJECT", "OBJECT_GROUP", "BOTH"]
    required: StrictBool
    source_ast_paths: list[StrictStr]


class CompiledRuleToolDependency(StrictModel):
    rule_ref: AssetVersionRef
    tool_ref: AssetVersionRef
    step_key: StrictStr
    fact_key: StrictStr
    target_scope: Literal["TARGET", "GLOBAL"]
    required: StrictBool
    source_ast_paths: list[StrictStr]


class EvidenceDependencyRef(StrictModel):
    source: Literal["observation", "tool"]
    dependency_id: StrictStr
    source_ast_paths: list[StrictStr]
    fact_status: FactAvailability
    required: StrictBool
    target_scope: Literal["OBJECT", "OBJECT_GROUP", "BOTH", "TARGET", "GLOBAL"]
    producer_ref: AssetVersionRef

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if (self.source == "tool") != (self.target_scope in {"TARGET", "GLOBAL"}):
            raise ValueError("dependency scope does not match its source")
        return self


class _CategoryEvidenceDetails(StrictModel):
    """The evidence fields present in the §5 projected examples."""

    category_id: StrictStr
    status: EvidenceCompletenessStatus
    required_dependency_count: NonNegativeInt
    required_valid_count: NonNegativeInt
    required_failed_count: NonNegativeInt
    optional_dependency_count: NonNegativeInt
    optional_valid_count: NonNegativeInt
    optional_failed_count: NonNegativeInt
    missing_dependency_refs: list[EvidenceDependencyRef]
    error_dependency_refs: list[EvidenceDependencyRef]
    unavailable_dependency_refs: list[EvidenceDependencyRef]

    @model_validator(mode="after")
    def validate_completeness(self) -> Self:
        if (
            self.required_valid_count + self.required_failed_count
            != self.required_dependency_count
        ):
            raise ValueError("required dependency counts inconsistent")
        if (
            self.optional_valid_count + self.optional_failed_count
            != self.optional_dependency_count
        ):
            raise ValueError("optional dependency counts inconsistent")
        expected = "COMPLETE"
        if self.required_failed_count:
            expected = "UNAVAILABLE"
        elif self.optional_failed_count:
            expected = "PARTIAL"
        if self.status != expected:
            raise ValueError("evidence status inconsistent with failure counts")

        failures = []
        for status, refs in (
            ("MISSING", self.missing_dependency_refs),
            ("ERROR", self.error_dependency_refs),
            ("UNAVAILABLE", self.unavailable_dependency_refs),
        ):
            if any(r.fact_status != status for r in refs):
                raise ValueError(f"invalid fact status in {status} list")
            keys = [(r.source, r.dependency_id) for r in refs]
            if keys != sorted(keys):
                raise ValueError(
                    "failure references must be sorted by source and dependency_id"
                )
            failures.extend(refs)
        require_unique(
            ((r.source, r.dependency_id) for r in failures), "failed dependency"
        )
        required = sum(r.required for r in failures)
        if (required, len(failures) - required) != (
            self.required_failed_count,
            self.optional_failed_count,
        ):
            raise ValueError("required/optional failure references do not match counts")
        return self


class CategoryEvidenceCompleteness(_CategoryEvidenceDetails):
    image_execution_id: StrictStr
    target_instance_id: StrictStr
    rule_ref: AssetVersionRef
    n04_final_attempt_id: StrictStr | None
    n05_execution_id: StrictStr | None
