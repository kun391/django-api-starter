"""Read-only release gate. Only merged, CI-verified source can be published."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REQUIRED_JOBS = {"test", "Optional Redis integration", "Private storage integration", "Production smoke"}


def validate_inputs(sha, version, repo, ref):
    if ref != "refs/heads/main":
        raise ValueError("Run the release workflow from main only.")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Release SHA must be a full lowercase commit SHA.")
    if not re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version) or len(version) > 40:
        raise ValueError("Version must be a stable vMAJOR.MINOR.PATCH identifier.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid repository identity.")


def select_ci_run(runs, sha, repo):
    matching = [run for run in runs if (
        run.get("head_sha") == sha and run.get("head_branch") == "main"
        and run.get("event") == "push" and run.get("path") == ".github/workflows/ci.yml"
        and run.get("head_repository", {}).get("full_name") == repo
    )]
    if not matching:
        raise ValueError("No main push CI run exists for the requested SHA.")
    latest = max(matching, key=lambda run: int(run["id"]))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError("The latest main CI run for this SHA has not passed.")
    return int(latest["id"])


def validate_jobs(jobs):
    passed = {job["name"] for job in jobs if job.get("status") == "completed" and job.get("conclusion") == "success"}
    if not REQUIRED_JOBS <= passed:
        raise ValueError("Required CI jobs are missing, skipped or unsuccessful.")


def api_get(path):
    request = Request("https://api.github.com" + path, headers={
        "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
    })
    with urlopen(request, timeout=20) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("GitHub response exceeds the release-gate bound.")
    return json.loads(data)


def main():
    sha, version = os.environ["RELEASE_SHA"], os.environ["RELEASE_VERSION"]
    repo = os.environ["GITHUB_REPOSITORY"]
    validate_inputs(sha, version, repo, os.environ["GITHUB_REF"])
    # All subprocess inputs are validated, separate arguments; no shell expansion.
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], check=True, capture_output=True)
    subprocess.run(["git", "cat-file", "-e", sha + ":scripts/smoke_production.sh"], check=True, capture_output=True)
    query = urlencode({"branch": "main", "event": "push", "head_sha": sha, "per_page": 100})
    result = api_get(f"/repos/{repo}/actions/workflows/ci.yml/runs?{query}")
    run_id = select_ci_run(result.get("workflow_runs", []), sha, repo)
    jobs = api_get(f"/repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")
    validate_jobs(jobs.get("jobs", []))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"sha={sha}\nversion={version}\nimage=ghcr.io/{repo.lower()}\n")
    print(f"Release source verified against main CI run {run_id}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not expose GitHub response bodies, credentials or subprocess output.
        print(f"Release verification failed ({type(error).__name__}); check inputs, ancestry and main CI.", file=sys.stderr)
        sys.exit(1)
