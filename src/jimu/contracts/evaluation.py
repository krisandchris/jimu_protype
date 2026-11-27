"""Runtime inputs to a single rule evaluation (D05 §9.3 and WP-A1 §2.4)."""

from typing import Literal, Self

from pydantic import StrictStr, model_validator

from .base import AssetVersionRef, StrictModel, TargetKind


class RuleTargetEvaluation(StrictModel):
    rule_ref: AssetVersionRef
    target_instance_id: StrictStr
    target_kind: TargetKind
    category_id: StrictStr
    applicable_subject_ref: AssetVersionRef


class ResolvedFactRef(StrictModel):
    source: Literal["observation", "tool"]
    image_execution_id: StrictStr
    target_instance_id: StrictStr | None
    question_ref: AssetVersionRef | None = None
    tool_ref: AssetVersionRef | None = None
    field_key: StrictStr | None = None
    fact_key: StrictStr | None = None
    step_key: StrictStr | None = None
    scope: Literal["TARGET", "GLOBAL"]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if (self.scope == "GLOBAL") != (self.target_instance_id is None):
            raise ValueError("fact target identity does not match scope")
        if self.source == "observation":
            if (
                self.scope != "TARGET"
                or self.question_ref is None
                or self.field_key is None
            ):
                raise ValueError("observation requires a target, question and field")
            if any(
                v is not None for v in (self.tool_ref, self.step_key, self.fact_key)
            ):
                raise ValueError("observation contains tool identity")
        else:
            if self.tool_ref is None or self.step_key is None or self.fact_key is None:
                raise ValueError("tool requires a producer, step and fact key")
            if self.question_ref is not None or self.field_key is not None:
                raise ValueError("tool contains observation identity")
        return self
