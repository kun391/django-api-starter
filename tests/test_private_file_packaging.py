"""Guard explicit ignore rules for data under the default private file root."""

from pathlib import Path

import pytest


@pytest.mark.parametrize("filename", [".gitignore", ".dockerignore"])
def test_default_private_file_root_has_an_explicit_exclusion(filename):
    root = Path(__file__).resolve().parent.parent
    rules = (root / filename).read_text(encoding="utf-8").splitlines()
    assert "private-files/" in rules
