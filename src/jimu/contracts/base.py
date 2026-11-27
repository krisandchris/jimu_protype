"""Base types shared by the frozen JIMU contracts."""

from typing import Annotated, TypeAlias

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


def _require_json_number(value: object) -> object:
    # StrictFloat also accepts Python objects with a __float__ method.
    if type(value) not in (float, int):
        raise ValueError("score must be a JSON number")
    return value


FiniteScore: TypeAlias = Annotated[
    Annotated[StrictFloat, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    | Annotated[StrictInt, Field(ge=0, le=1)],
    BeforeValidator(_require_json_number),
]


class AssetVersionRef(StrictModel):
    """A reference to one specific revision of an asset (WP-A1 §2.1)."""

    asset_id: StrictStr = Field(min_length=1)
    version: StrictStr = Field(min_length=1)
    content_hash: StrictStr = Field(min_length=1)
