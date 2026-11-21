"""Commit messages of formatting-only commits in the analysis windows, grouped by what they say.

Usage: python -m churn.messages

Treeless clones (commits only) are enough to read messages. Writes
data/formatting_only_messages.csv; the analysis reads it to describe RQ2.
"""
import csv
import os
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

from .analyze import commits, load_sample, parse
from .profile import CLONE_DIR, remove_clone
from .sample import WORKERS, clone_urls

OUT = "data/formatting_only_messages.csv"
FIELDS = ["group", "name", "period", "sha", "author", "formatting_lines", "category", "subject"]

# First match wins. Order matters: an automated pre-commit.ci fix that mentions Black is a bot commit.
CATEGORIES = [
    ("bot", re.compile(r"pre-commit\.ci|\[bot\]|auto ?fix|autoformat|github-actions", re.I)),
    ("black", re.compile(r"\bblack\b|\bblacken", re.I)),
    ("other tool", re.compile(r"\bruff\b|\bisort\b|\byapf\b|autopep8|flake8|pylint|pycodestyle|pyflakes|pre-?commit|\blint", re.I)),
    ("style", re.compile(r"pep ?8|format|style|whitespace|indent|cosmetic|tidy|clean ?-?up|trailing|line length|long lines|quotes|spacing|typo", re.I)),
]


def category(author, subject):
    for name, pattern in CATEGORIES:
        if pattern.search(author + " " + subject):
            return name
    return "other"


def messages(name, url, wanted):
    repo = os.path.join(CLONE_DIR, "msg", name.replace("/", "__"))
    try:
        remove_clone(repo)
        subprocess.run(["git", "clone", "--quiet", "--bare", "--filter=tree:0", url, repo], check=True)
        out = subprocess.run(["git", "-C", repo, "log", "--no-walk=unsorted", "--format=%H%x00%an <%ae>%x00%s", *wanted],
                             capture_output=True, check=True).stdout.decode("utf-8", "replace")
        return {sha: (author, subject) for sha, author, subject in (line.split("\0") for line in out.splitlines())}
    finally:
        remove_clone(repo)


def main():
    adopters, pairs = load_sample()
    urls = clone_urls()
    jobs = []
    for pair in pairs:
        adopter = adopters[pair["adopter"]]
        date = parse(adopter["adoption_date"])
        skip = adopter["a2_sha"] if adopter["a2_confirmed"] == "True" else None
        for group, name in (("adopter", pair["adopter"]), ("control", pair["control"])):
            rows = [r for r in commits(name, date, skip if group == "adopter" else None) if r["kind"] == "formatting_only"]
            if rows:
                jobs.append((group, name, rows))
    with ProcessPoolExecutor(WORKERS) as pool, open(OUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, FIELDS)
        writer.writeheader()
        found = pool.map(messages, [j[1] for j in jobs], [urls[j[1]] for j in jobs], [[r["sha"] for r in j[2]] for j in jobs])
        for (group, name, rows), by_sha in zip(jobs, found):
            for r in rows:
                author, subject = by_sha[r["sha"]]
                writer.writerow({"group": group, "name": name, "period": "pre" if r["offset"] < 0 else "post", "sha": r["sha"],
                                 "author": author, "formatting_lines": r["f"], "category": category(author, subject), "subject": subject})
            print(name, len(rows), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
