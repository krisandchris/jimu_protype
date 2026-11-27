"""Check imports and construction of compiler-owned objects against AGENTS.md §2."""

import ast
from collections.abc import Iterator
from importlib.util import resolve_name
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
FORBIDDEN_IMPORTS = {
    "contracts": {"control_plane", "compiler", "runtime"},
    "runtime": {"control_plane", "compiler"},
}
COMPILER_PRODUCTS = {
    "CompiledBundle",
    "CompiledRuleFieldDependency",
    "CompiledRuleToolDependency",
}
MODEL_FACTORIES = {
    "model_construct",
    "model_validate",
    "model_validate_json",
    "model_validate_strings",
}


def _imports(tree: ast.Module, package: str) -> Iterator[tuple[ast.alias, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = resolve_name("." * node.level + (node.module or ""), package)
            for alias in node.names:
                yield alias, f"{module}.{alias.name}"


def _boundary_errors(source: str, package: str) -> list[str]:
    tree = ast.parse(source)
    layer = package.split(".")[1]
    forbidden = FORBIDDEN_IMPORTS.get(layer, set())
    errors = []
    aliases = {}
    for alias, target in _imports(tree, package):
        aliases[alias.asname or alias.name] = target
        parts = target.removeprefix("src.").split(".")
        if len(parts) >= 2 and parts[0] == "jimu" and parts[1] in forbidden:
            errors.append(f"line {alias.lineno}: {layer} imports {target}")

    if layer == "control_plane":
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            callee = call.func
            if isinstance(callee, ast.Attribute) and callee.attr in MODEL_FACTORIES:
                callee = callee.value
            head, dot, tail = ast.unparse(callee).partition(".")
            name = (aliases.get(head, head) + dot + tail).removeprefix("src.")
            if name in COMPILER_PRODUCTS or (
                name.startswith("jimu.")
                and name.rsplit(".", 1)[-1] in COMPILER_PRODUCTS
            ):
                errors.append(f"line {call.lineno}: {layer} constructs {name}")
    return errors


@pytest.mark.parametrize("layer", ["contracts", "control_plane", "runtime"])
def test_layer_boundaries(layer: str) -> None:
    paths = sorted((SRC / "jimu" / layer).rglob("*.py"))
    assert paths, f"Missing source package jimu.{layer}"
    errors = []
    for path in paths:
        package = ".".join(path.parent.relative_to(SRC).parts)
        for error in _boundary_errors(path.read_text(encoding="utf-8"), package):
            errors.append(f"{path.relative_to(SRC)}: {error}")
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize(
    ("package", "source"),
    [
        ("jimu.contracts", "import jimu.compiler.packing as packing"),
        ("jimu.contracts", "from src.jimu import runtime"),
        ("jimu.contracts", "from ..control_plane import assets"),
        ("jimu.runtime.evidence", "from ... import control_plane as assets"),
        ("jimu.runtime", "from jimu import compiler"),
        ("jimu.control_plane", "from ..contracts import CompiledBundle as B\nB()"),
        (
            "jimu.control_plane.api",
            "from ... import contracts as c\nc.CompiledRuleFieldDependency()",
        ),
        (
            "jimu.control_plane",
            "from ..contracts import CompiledRuleToolDependency as Dep\n"
            "Dep.model_validate({})",
        ),
    ],
)
def test_forbidden_dependencies_are_detected(package: str, source: str) -> None:
    assert _boundary_errors(source, package)


@pytest.mark.parametrize(
    ("package", "source"),
    [
        ("jimu.runtime.evidence", "from ...contracts import AssetVersionRef"),
        ("jimu.contracts", "from .base import StrictModel"),
        (
            "jimu.control_plane",
            "from jimu.contracts import CompiledBundle\n"
            "from jimu.compiler import compile_bundle\n"
            "bundle: CompiledBundle = compile_bundle()",
        ),
    ],
)
def test_permitted_dependencies_are_accepted(package: str, source: str) -> None:
    assert _boundary_errors(source, package) == []
