"""Architecture checker and module metadata contract regressions."""

from pathlib import Path

import pytest
import yaml

from scripts import check_architecture, create_module


def test_repository_architecture():
    assert check_architecture.check() == []


@pytest.fixture
def isolated_apps(tmp_path, monkeypatch):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    monkeypatch.setattr(check_architecture, "APPS_DIR", apps_dir)
    monkeypatch.setattr(create_module, "APPS_DIR", apps_dir)
    return apps_dir


def add_module(
    root: Path,
    name: str,
    *,
    kind: str = "business",
    architecture: str = "simple",
    depends_on: list | None = None,
) -> Path:
    path = root / name
    path.mkdir()
    (path / "__init__.py").write_text("")
    (path / "README.md").write_text(f"# {name}\n")
    metadata = {
        "name": name,
        "kind": kind,
        "architecture": architecture,
        "depends_on": [] if depends_on is None else depends_on,
    }
    (path / "module.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8"
    )
    return path


def assert_problem(expected: str) -> None:
    errors = check_architecture.check()
    assert any(expected in message for message in errors), errors


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("kind", "medium", "kind must be one of"),
        ("architecture", "domain", "architecture must be one of"),
        ("kind", ["business"], "kind must be one of"),
        ("architecture", None, "architecture must be one of"),
        ("depends_on", "orders", "depends_on must be a list"),
        ("depends_on", [42], "depends_on must be a list"),
    ],
)
def test_metadata_invalid_values_fail_closed(isolated_apps, field, value, message):
    path = add_module(isolated_apps, "orders")
    metadata = yaml.safe_load((path / "module.yaml").read_text())
    metadata[field] = value
    (path / "module.yaml").write_text(yaml.safe_dump(metadata))
    assert_problem(message)


def test_legacy_type_is_not_silently_accepted(isolated_apps):
    path = add_module(isolated_apps, "orders")
    with (path / "module.yaml").open("a") as stream:
        stream.write("type: domain\n")
    assert_problem("legacy type is ambiguous")


@pytest.mark.parametrize("content", ["- not-a-mapping\n", "null\n"])
def test_metadata_requires_mapping(isolated_apps, content):
    path = add_module(isolated_apps, "orders")
    (path / "module.yaml").write_text(content)
    assert_problem("module.yaml must be a mapping")


def test_declared_dependencies_form_a_dag(isolated_apps):
    add_module(isolated_apps, "alpha", depends_on=["beta"])
    add_module(isolated_apps, "beta", depends_on=["gamma"])
    add_module(isolated_apps, "gamma")
    assert check_architecture.check() == []


def test_indirect_cycle_detected_even_with_no_imports(isolated_apps):
    add_module(isolated_apps, "alpha", depends_on=["beta"])
    add_module(isolated_apps, "beta", depends_on=["gamma"])
    add_module(isolated_apps, "gamma", depends_on=["alpha"])
    assert_problem("dependency cycle: alpha -> beta -> gamma -> alpha")


def test_self_cycle_detected(isolated_apps):
    add_module(isolated_apps, "alpha", depends_on=["alpha"])
    assert_problem("dependency cycle: alpha -> alpha")


def test_duplicate_and_unknown_dependencies_detected(isolated_apps):
    add_module(isolated_apps, "alpha", depends_on=["unknown", "unknown"])
    assert_problem("duplicate depends_on entries")
    assert_problem("unknown depends_on entries: unknown")


def test_undeclared_import_is_still_rejected(isolated_apps):
    path = add_module(isolated_apps, "alpha")
    add_module(isolated_apps, "beta")
    (path / "service.py").write_text("from apps.beta.services import do_work\n")
    assert_problem("imports undeclared modules: beta")


@pytest.mark.parametrize("layer", ["domain", "ports", "adapters"])
def test_simple_forbids_premature_architecture_layers(isolated_apps, layer):
    path = add_module(isolated_apps, "alpha")
    (path / layer).mkdir()
    assert_problem(f"simple architecture cannot include {layer}/")


def test_medium_does_not_claim_to_infer_complexity_from_files(isolated_apps):
    path = add_module(isolated_apps, "alpha", architecture="medium")
    (path / "services.py").write_text("def work():\n    pass\n")
    (path / "domain").mkdir()
    assert check_architecture.check() == []


@pytest.mark.parametrize("preset", sorted(create_module.PRESETS))
def test_current_generator_metadata_passes_checker(isolated_apps, preset):
    path = create_module.create_module("example", preset)
    metadata = yaml.safe_load((path / "module.yaml").read_text())
    assert metadata["kind"] in check_architecture.MODULE_KINDS
    assert metadata["architecture"] in check_architecture.ARCHITECTURE_LEVELS
    assert "type" not in metadata
    assert check_architecture.check() == []
