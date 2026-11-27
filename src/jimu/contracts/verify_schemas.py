"""Export the validation JSON Schema of each shared contract to stdout."""

import json
from importlib import import_module
from inspect import getmembers, isclass
from pkgutil import walk_packages

from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import JsonSchemaValue

from jimu import contracts
from jimu.contracts import base


def export_schemas() -> dict[str, JsonSchemaValue]:
    schemas = {
        f"{base.__name__}.FiniteScore": TypeAdapter(base.FiniteScore).json_schema()
    }
    modules = [contracts.__name__]
    modules.extend(m.name for m in walk_packages(contracts.__path__, "jimu.contracts."))
    for module_name in modules:
        for _, model in getmembers(import_module(module_name), isclass):
            if model.__module__ != module_name or not issubclass(model, BaseModel):
                continue
            name = f"{module_name}.{model.__qualname__}"
            if not issubclass(model, base.StrictModel):
                raise TypeError(f"{name} must inherit StrictModel")
            schemas[name] = model.model_json_schema()
    return schemas


if __name__ == "__main__":
    print(json.dumps(export_schemas(), indent=2, sort_keys=True, allow_nan=False))
