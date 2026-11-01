"""Write one CSV row per non-merge commit with Python churn split by category.

Usage: python -m churn.collect REPO_DIR OUT_CSV
"""
import csv
import re
import subprocess
import sys
from collections import Counter

from .classify import COMMENT, FORMATTING, LOGIC, UNPARSED, Hunk, classify_hunks

MARKER = b"\x00COMMIT\t"
HUNK_RE = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))? @@")
INDEX_RE = re.compile(rb"^index ([0-9a-f]+)\.\.([0-9a-f]+)")
NULL_BLOB = re.compile(rb"^0+$")

FIELDS = [
    "sha", "author_date", "commit_date", "author", "py_files", "hunks", "added", "deleted",
    "formatting_lines", "comment_lines", "logic_lines", "unparsed_lines", "new_or_deleted_lines", "kind",
]


class Blobs:
    """Read blobs through one long-lived `git cat-file --batch` process."""

    def __init__(self, repo):
        self.proc = subprocess.Popen(
            ["git", "-C", repo, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )

    def read(self, sha):
        self.proc.stdin.write(sha + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        if header[-1] == b"missing":
            return None
        data = self.proc.stdout.read(int(header[2]))
        self.proc.stdout.read(1)
        return data.decode("utf-8", "surrogateescape")

    def close(self):
        self.proc.stdin.close()
        self.proc.wait()


def _commit_kind(stats):
    if stats["hunks"] == 0:
        return "no_python_change"
    if stats[FORMATTING] and not (stats[COMMENT] or stats[LOGIC] or stats[UNPARSED] or stats["new_or_deleted"]):
        return "formatting_only"
    if stats[UNPARSED] and not stats[LOGIC]:
        return "unknown"
    return "mixed" if stats[FORMATTING] else "other"


def _finish_file(current, stats, blobs):
    if current is None or not current["hunks"]:
        return
    stats["py_files"] += 1
    stats["hunks"] += len(current["hunks"])
    stats["added"] += sum(len(h.added) for h in current["hunks"])
    stats["deleted"] += sum(h.old_count for h in current["hunks"])
    lines = [h.old_count + len(h.added) for h in current["hunks"]]
    if NULL_BLOB.match(current["old"]) or NULL_BLOB.match(current["new"]):
        stats["new_or_deleted"] += sum(lines)
        return
    old_source = blobs.read(current["old"])
    new_source = blobs.read(current["new"])
    if old_source is None:
        stats[UNPARSED] += sum(lines)
        return
    for category, count in zip(classify_hunks(old_source, new_source, current["hunks"]), lines):
        stats[category] += count


def iter_commits(repo):
    blobs = Blobs(repo)
    proc = subprocess.Popen(
        ["git", "-C", repo, "-c", "core.quotepath=off", "log", "--no-merges", "--reverse", "--full-index",
         "--no-color", "--no-ext-diff", "-M", "-U0", "--diff-algorithm=histogram",
         "--format=%x00COMMIT%x09%H%x09%aI%x09%cI%x09%ae", "-p", "--", "*.py"],
        stdout=subprocess.PIPE,
    )
    meta, stats, current, hunk, stale = None, Counter(), None, None, False
    for raw in proc.stdout:
        if raw.startswith(MARKER):
            _finish_file(current, stats, blobs)
            if meta:
                yield _row(meta, stats)
            meta = raw[len(MARKER):].decode("utf-8", "replace").rstrip("\r\n").split("\t")
            stats, current, hunk = Counter(), None, None
        elif raw.startswith(b"diff --git "):
            _finish_file(current, stats, blobs)
            current, hunk = {"old": b"", "new": b"", "hunks": []}, None
        elif hunk is None and raw.startswith(b"index ") and current is not None:
            match = INDEX_RE.match(raw)
            if match:
                current["old"], current["new"] = match.group(1), match.group(2)
        elif raw.startswith(b"@@ ") and current is not None:
            match = HUNK_RE.match(raw)
            old_count = int(match.group(2)) if match.group(2) is not None else 1
            hunk = Hunk(int(match.group(1)), old_count, [])
            current["hunks"].append(hunk)
            stale = False
        elif hunk is not None:
            if raw.startswith(b"+"):
                hunk.added.append(raw[1:].decode("utf-8", "surrogateescape"))
                stale = False
            elif raw.startswith(b"-"):
                stale = True
            elif raw.startswith(b"\\") and not stale and hunk.added:
                # "\ No newline at end of file" after an added line.
                hunk.added[-1] = hunk.added[-1].rstrip("\r\n")
    _finish_file(current, stats, blobs)
    if meta:
        yield _row(meta, stats)
    blobs.close()
    if proc.wait() != 0:
        raise RuntimeError(f"git log failed for {repo}")


def _row(meta, stats):
    return {
        "sha": meta[0], "author_date": meta[1], "commit_date": meta[2], "author": meta[3],
        "py_files": stats["py_files"], "hunks": stats["hunks"],
        "added": stats["added"], "deleted": stats["deleted"],
        "formatting_lines": stats[FORMATTING], "comment_lines": stats[COMMENT],
        "logic_lines": stats[LOGIC], "unparsed_lines": stats[UNPARSED],
        "new_or_deleted_lines": stats["new_or_deleted"], "kind": _commit_kind(stats),
    }


def main(repo, out_csv):
    with open(out_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, FIELDS)
        writer.writeheader()
        for row in iter_commits(repo):
            writer.writerow(row)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
