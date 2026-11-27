"""WP-A2 §2. Only N06RawModelOutput is sent to guided decoding."""

from enum import StrEnum
from typing import Annotated, TypeAlias

from pydantic import AfterValidator, Field, StrictStr, WithJsonSchema

from .base import FiniteScore, NonNegativeInt, StrictModel

# Both interfaces validate the same strict JSON numbers. WP-A2 explicitly
# represents an accepted integer as a float; shared FiniteScore preserves it.
FiniteUnitFloat: TypeAlias = Annotated[
    FiniteScore,
    AfterValidator(float),
    WithJsonSchema({"type": "number", "minimum": 0.0, "maximum": 1.0}),
]


class RawCategoryEvidence(StrictModel):
    visual_evidence_indices: list[NonNegativeInt]
    observation_evidence_indices: list[NonNegativeInt]
    tool_evidence_indices: list[NonNegativeInt]


class RawRankedCategory(StrictModel):
    category_index: NonNegativeInt
    score: FiniteUnitFloat
    reasoning: StrictStr
    evidence: RawCategoryEvidence


class RawTargetJEVResult(StrictModel):
    target_ref_index: NonNegativeInt
    ranked_categories: list[RawRankedCategory]
    non_violation_score: FiniteUnitFloat


class N06RawModelOutput(StrictModel):
    results: list[RawTargetJEVResult] = Field(min_length=1)


class N06RawEnvelope(StrictModel):
    # Stage 1 preserves individual JSON objects, including malformed targets.
    # Stage 2 binds the index before applying RawTargetJEVResult validation.
    results: list[dict[str, object]] = Field(min_length=1)


class N06ErrorCode(StrEnum):
    N06_JSON_PARSE_FAILED = "n06_json_parse_failed"
    N06_SCHEMA_VALIDATION_FAILED = "n06_schema_validation_failed"
    N06_OUTPUT_TRUNCATED = "n06_output_truncated"
    N06_TARGET_REFERENCE_INVALID = "n06_target_reference_invalid"
    N06_TARGET_MISSING = "n06_target_missing"
    N06_CATEGORY_INDEX_INVALID = "n06_category_index_invalid"
    N06_SCORE_OUT_OF_RANGE = "n06_score_out_of_range"
    N06_DUPLICATE_CATEGORY = "n06_duplicate_category"
    N06_DUPLICATE_TARGET = "n06_duplicate_target"
    N06_EVIDENCE_REFERENCE_INVALID = "n06_evidence_reference_invalid"
    N06_CATEGORY_OUT_OF_ACTIVE_SCOPE = "category_out_of_active_scope"
