"""Generated Django modules follow the same minimal conventions as the repo."""

import sys

import pytest
import yaml

from scripts import check_architecture, create_module


@pytest.fixture
def module_workspace(tmp_path, monkeypatch):
    apps = tmp_path / "apps"
    apps.mkdir()
    monkeypatch.setattr(create_module, "ROOT", tmp_path)
    monkeypatch.setattr(create_module, "APPS_DIR", apps)
    monkeypatch.setattr(check_architecture, "APPS_DIR", apps)
    return apps


@pytest.mark.parametrize("preset", sorted(create_module.PRESETS))
def test_all_presets_are_flat_and_architecture_valid(module_workspace, preset):
    directory = create_module.create_module("example", preset)
    assert (directory / "apps.py").is_file()
    assert (directory / "README.md").is_file()
    assert (directory / "tests" / "__init__.py").is_file()
    assert not any((directory / layer).exists() for layer in (
        "domain", "ports", "adapters", "services", "selectors",
    ))
    metadata = yaml.safe_load((directory / "module.yaml").read_text())
    assert metadata["kind"] in check_architecture.MODULE_KINDS
    assert metadata["architecture"] in check_architecture.ARCHITECTURE_LEVELS
    assert check_architecture.check() == []


def test_crud_has_only_essential_files(module_workspace):
    module = create_module.create_module("orders", "crud")
    assert (module / "models.py").is_file()
    assert (module / "api" / "urls.py").is_file()
    assert (module / "migrations" / "__init__.py").is_file()
    assert not (module / "services.py").exists()
    assert not (module / "tasks.py").exists()


@pytest.mark.parametrize("preset", ["domain", "integration"])
def test_service_presets_use_flat_services_file(module_workspace, preset):
    module = create_module.create_module("orders", preset)
    assert (module / "services.py").is_file()
    assert not (module / "selectors.py").exists()
    assert not (module / "provider.py").exists()
    assert not (module / "transport.py").exists()
    assert not (module / "domain").exists()
    assert not (module / "ports").exists()
    assert not (module / "adapters").exists()


def test_event_consumer_has_no_unused_api_or_models(module_workspace):
    module = create_module.create_module("consumer", "event-consumer")
    assert (module / "services.py").is_file()
    assert (module / "tasks.py").is_file()
    assert not (module / "models.py").exists()
    assert not (module / "api").exists()
    assert not (module / "migrations").exists()


def test_kind_and_architecture_can_be_overridden_independently(module_workspace):
    module = create_module.create_module(
        "maintenance", "crud", kind="support", architecture="medium"
    )
    metadata = yaml.safe_load((module / "module.yaml").read_text())
    assert metadata["kind"] == "support"
    assert metadata["architecture"] == "medium"
    assert check_architecture.check() == []


@pytest.mark.parametrize(
    ("name", "preset", "kind", "architecture"),
    [
        ("../escape", "crud", None, None),
        ("core", "crud", None, None),
        ("example", "no-such-preset", None, None),
        ("example", "crud", "bad", None),
        ("example", "crud", None, "bad"),
        ("example", "crud", "", None),
    ],
)
def test_programmatic_generator_rejects_bad_inputs(
    module_workspace, name, preset, kind, architecture
):
    with pytest.raises(ValueError):
        create_module.create_module(
            name, preset, kind=kind, architecture=architecture
        )


def test_generator_rejects_existing_module(module_workspace):
    create_module.create_module("orders", "crud")
    with pytest.raises(FileExistsError):
        create_module.create_module("orders", "domain")


def test_preset_flag_default_and_legacy_alias(module_workspace, monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["create_module.py", "with_preset", "--preset", "domain"]
    )
    assert create_module.main() == 0
    assert "deprecated" not in capsys.readouterr().err.lower()
    assert (module_workspace / "with_preset" / "services.py").is_file()

    monkeypatch.setattr(
        sys, "argv", ["create_module.py", "legacy", "--type", "crud"]
    )
    assert create_module.main() == 0
    assert "deprecated" in capsys.readouterr().err.lower()
    assert (module_workspace / "legacy" / "models.py").is_file()

    monkeypatch.setattr(sys, "argv", ["create_module.py", "default"])
    assert create_module.main() == 0
    assert (module_workspace / "default" / "models.py").is_file()
    assert not (module_workspace / "default" / "services.py").exists()
    assert check_architecture.check() == []


def test_mixing_alias_and_new_flag_is_error(module_workspace, monkeypatch):
    monkeypatch.setattr(
        sys, "argv",
        ["create_module.py", "module", "--type", "crud", "--preset", "domain"],
    )
    with pytest.raises(SystemExit) as exc:
        create_module.main()
    assert exc.value.code == 2
    assert not (module_workspace / "module").exists()
