"""Base types shared by the frozen JIMU contracts."""

from collections.abc import Hashable, Iterable
from typing import Annotated, Literal, TypeAlias

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


NonNegativeInt: TypeAlias = Annotated[StrictInt, Field(ge=0)]
TargetKind: TypeAlias = Literal["OBJECT", "OBJECT_GROUP"]


def require_unique(values: Iterable[Hashable], field: str) -> None:
    seen: set[Hashable] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {field}: {value}")
        seen.add(value)


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
