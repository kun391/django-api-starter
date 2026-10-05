from scripts.check_architecture import check


def test_repository_architecture():
    assert check() == []
