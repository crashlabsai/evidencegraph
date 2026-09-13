"""The files copied into /app must work without the Evidencegraph installation."""

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from evidencegraph.lab.runtime import runtime_io

RUNTIME = Path(__file__).parents[1] / "src" / "evidencegraph" / "lab" / "runtime"
MODULES = {path.stem for path in RUNTIME.glob("*.py")}


def unexpected_imports(source: str) -> set[str]:
    tree = ast.parse(source, feature_version=(3, 12))
    imports = set()
    # Walk inside functions, classes and conditional branches as well as the module.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 1:
                imports.add("parent-package")
            elif node.module:
                imports.add(node.module.split(".")[0])
            else:
                imports.update(alias.name for alias in node.names)
    return imports - sys.stdlib_module_names - MODULES


@pytest.mark.parametrize("path", sorted(RUNTIME.glob("*.py")), ids=lambda path: path.name)
def test_runtime_is_python312_syntax_and_stdlib_only(path):
    assert not unexpected_imports(path.read_text())


def test_import_boundary_rejects_imports_inside_functions_and_classes():
    source = """if True:
    def save():
        from evidencegraph.manifest import atomic_json
    class Schema:
        import pydantic
"""
    assert unexpected_imports(source) == {"evidencegraph", "pydantic"}


def test_flat_runtime_imports_without_site_packages(tmp_path):
    for path in RUNTIME.glob("*.py"):
        shutil.copyfile(path, tmp_path / path.name)
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    code = (
        "import collection, runtime_io, registry, server, client, registry_client; "
        "from pathlib import Path; "
        "runtime_io.atomic_json(Path('saved.json'), {'ok': True}); "
        "assert registry_client.request is client.request; "
        "assert server.Registry.__bases__ == (registry.Registry,)"
    )
    completed = subprocess.run(
        [sys.executable, "-S", "-c", code],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"ok": true' in (tmp_path / "saved.json").read_text()


def test_manifest_and_compatibility_modules_share_runtime_helpers():
    from evidencegraph.lab.collection import Collector
    from evidencegraph.lab.io import save_json
    from evidencegraph.lab.registry import Registry
    from evidencegraph.lab.runtime.collection import Collector as RuntimeCollector
    from evidencegraph.lab.runtime.registry import Registry as RuntimeRegistry
    from evidencegraph.manifest import atomic_json

    assert Collector is RuntimeCollector
    assert Registry is RuntimeRegistry
    assert atomic_json is runtime_io.atomic_json
    assert save_json is runtime_io.save_json


def test_atomic_json_keeps_previous_file_and_cleans_temporary_on_serialization_error(tmp_path):
    destination = tmp_path / "value.json"
    runtime_io.atomic_json(destination, {"before": "unchanged"})
    before = destination.read_bytes()
    with pytest.raises(ValueError, match="Out of range float"):
        runtime_io.atomic_json(destination, {"after": float("nan")})
    assert destination.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["value.json"]


def test_atomic_json_keeps_previous_file_when_replace_fails(tmp_path, monkeypatch):
    destination = tmp_path / "value.json"
    runtime_io.atomic_json(destination, {"before": True})
    before = destination.read_bytes()

    def fail_replace(*args):
        raise OSError("simulated failed replace")

    monkeypatch.setattr(runtime_io.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated failed replace"):
        runtime_io.atomic_json(destination, {"after": True})
    assert destination.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["value.json"]
