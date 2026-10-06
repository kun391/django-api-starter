"""Release gate failure modes without network writes or package publication."""

from copy import deepcopy

import pytest

from scripts.verify_release import REQUIRED_JOBS, select_ci_run, validate_inputs, validate_jobs

SHA = "a" * 40
REPO = "example/starter"


def passed_run():
    return {"id": 12, "head_sha": SHA, "head_branch": "main", "event": "push",
            "path": ".github/workflows/ci.yml", "head_repository": {"full_name": REPO},
            "status": "completed", "conclusion": "success"}


def test_valid_release_identity():
    validate_inputs(SHA, "v1.2.3", REPO, "refs/heads/main")
    assert select_ci_run([passed_run()], SHA, REPO) == 12


@pytest.mark.parametrize("sha,version,repo,ref", [
    ("main", "v1.0.0", REPO, "refs/heads/main"),
    (SHA + "\nevil", "v1.0.0", REPO, "refs/heads/main"),
    (SHA, "v1.0.0;echo bad", REPO, "refs/heads/main"),
    (SHA, "v01.0.0", REPO, "refs/heads/main"),
    (SHA, "v1.0.0", "https://example.com", "refs/heads/main"),
    (SHA, "v1.0.0", REPO, "refs/heads/feature"),
])
def test_invalid_release_identity(sha, version, repo, ref):
    with pytest.raises(ValueError):
        validate_inputs(sha, version, repo, ref)


@pytest.mark.parametrize("field,value", [
    ("head_sha", "b" * 40), ("head_branch", "feature"), ("event", "pull_request"),
    ("path", ".github/workflows/other.yml"), ("head_repository", {"full_name": "fork/starter"}),
    ("status", "in_progress"), ("conclusion", "failure"),
])
def test_no_substitute_for_green_main_ci(field, value):
    run = passed_run()
    run[field] = value
    with pytest.raises(ValueError):
        select_ci_run([run], SHA, REPO)


def test_newer_failed_run_cannot_be_hidden_by_old_success():
    failed = {**passed_run(), "id": 13, "conclusion": "failure"}
    with pytest.raises(ValueError):
        select_ci_run([passed_run(), failed], SHA, REPO)


def test_all_required_jobs_must_pass_not_skip():
    jobs = [{"name": name, "status": "completed", "conclusion": "success"} for name in REQUIRED_JOBS]
    validate_jobs(jobs)
    for index in range(len(jobs)):
        bad = deepcopy(jobs)
        bad[index]["conclusion"] = "skipped"
        with pytest.raises(ValueError):
            validate_jobs(bad)
    with pytest.raises(ValueError):
        validate_jobs(jobs[:-1])
