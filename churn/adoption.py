"""Find when a repository first mentions Black in configuration or automation files.

Usage: python -m churn.adoption REPO_DIR

The first mention is only a candidate date: a config file does not prove that
Black is run. Enforcement files (pre-commit, CI) are reported separately from
declarations (pyproject, requirements), and both are checked against the largest
formatting-only commit in the data from churn.collect.
"""
import json
import subprocess
import sys

BLACK_RE = r"(psf|ambv|python)/black|\[tool\.black\]|(^|[^a-zA-Z_-])black([^a-zA-Z_-]|$)"
ENFORCEMENT = [".pre-commit-config.yaml", ".github/workflows", ".travis.yml", ".circleci", "tox.ini", "azure-pipelines.yml", "noxfile.py", "Makefile"]
DECLARATION = ["pyproject.toml", "setup.cfg", "setup.py", "requirements*.txt", "requirements/*", "dev-requirements*.txt", "Pipfile", "environment*.yml"]


def first_match(repo, paths):
    out = subprocess.run(
        ["git", "-C", repo, "log", "--reverse", "--no-merges", "--format=%H %cI", "-G", BLACK_RE, "--", *paths],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    if not out:
        return None
    sha, date = out[0].split()
    return {"sha": sha, "date": date}


def present_at_head(repo, paths):
    result = subprocess.run(
        ["git", "-C", repo, "grep", "-qE", r"psf/black|ambv/black|\[tool\.black\]|\bblack\b", "HEAD", "--", *paths],
        capture_output=True,
    )
    return result.returncode == 0


def detect(repo):
    return {
        "first_enforcement": first_match(repo, ENFORCEMENT),
        "first_declaration": first_match(repo, DECLARATION),
        "at_head": present_at_head(repo, ENFORCEMENT + DECLARATION),
    }


if __name__ == "__main__":
    print(json.dumps(detect(sys.argv[1]), indent=2))
