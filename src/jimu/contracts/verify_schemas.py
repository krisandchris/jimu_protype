"""Check the frozen examples and export public validation schemas to stdout."""

import argparse
import json
import sys
from importlib import import_module
from inspect import getmembers, isclass
from pathlib import Path
from pkgutil import walk_packages

from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import JsonSchemaValue

from jimu import contracts
from jimu.contracts import base, wp_a2_runtime, wp_a2_wire
from jimu.contracts._golden import verify_fixtures


def export_schemas() -> dict[str, JsonSchemaValue]:
    schemas = {
        f"{base.__name__}.FiniteScore": TypeAdapter(base.FiniteScore).json_schema(),
        f"{wp_a2_wire.__name__}.FiniteUnitFloat": TypeAdapter(
            wp_a2_wire.FiniteUnitFloat
        ).json_schema(),
        f"{wp_a2_runtime.__name__}.EvidenceRef": TypeAdapter(
            wp_a2_runtime.EvidenceRef
        ).json_schema(),
    }
    modules = [contracts.__name__]
    modules.extend(m.name for m in walk_packages(contracts.__path__, "jimu.contracts."))
    for module_name in modules:
        for _, model in getmembers(import_module(module_name), isclass):
            if (
                model.__module__ != module_name
                or model.__name__.startswith("_")
                or not issubclass(model, BaseModel)
            ):
                continue
            name = f"{module_name}.{model.__qualname__}"
            if not issubclass(model, base.StrictModel):
                raise TypeError(f"{name} must inherit StrictModel")
            schemas[name] = model.model_json_schema()
    return schemas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts-dir", type=Path, default=Path("docs/contracts"))
    args = parser.parse_args()
    schemas = export_schemas()
    count = verify_fixtures(args.contracts_dir)
    print(json.dumps(dict(sorted(schemas.items())), indent=2, allow_nan=False))
    print(
        f"Verified {len(schemas)} schemas and {count} golden fixture pairs.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
