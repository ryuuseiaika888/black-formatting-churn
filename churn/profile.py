"""Profile candidate repositories: start dates, Black usage history, and activity.

Usage: python -m churn.profile CANDIDATES_CSV OUT_CSV [LIMIT]

Clones are partial (`--filter=blob:none`) and go to CLONE_DIR (default %TEMP%/bc).
Only the blobs of configuration files are downloaded. See docs/protocol.md for
what each column means and how the dates are cross-checked.
"""
import csv
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import urllib.request
from datetime import datetime, timedelta, timezone

CLONE_DIR = os.environ.get("CLONE_DIR", os.path.join(os.environ.get("TEMP", "/tmp"), "bc"))
BLACK_FIRST_RELEASE = datetime(2018, 3, 14, tzinfo=timezone.utc)
WINDOW = timedelta(days=730)

MECHANISMS = {
    "pre_commit": [".pre-commit-config.yaml", ".pre-commit-config.yml"],
    "ci": [".github/workflows", ".travis.yml", ".circleci", "azure-pipelines.yml", ".gitlab-ci.yml", "appveyor.yml"],
    "task_runner": ["tox.ini", "noxfile.py", "Makefile"],
    "project_config": ["pyproject.toml", "setup.cfg", "setup.py"],
    "requirements": ["*requirements*.txt", "*requirements*.in", "Pipfile", "environment*.yml"],
}
PATHS = [path for paths in MECHANISMS.values() for path in paths]

TOOLS = {
    "black": re.compile(r"(?<![\w-])black(?![\w-])"),
    "ruff_format": re.compile(r"ruff-format|ruff format|\[tool\.ruff\.format\]"),
    "yapf": re.compile(r"(?<![\w-])yapf(?![\w-])"),
    "autopep8": re.compile(r"(?<![\w-])autopep8(?![\w-])"),
    "isort": re.compile(r"(?<![\w-])isort(?![\w-])"),
    "reorder_imports": re.compile(r"reorder[-_]python[-_]imports"),
}
BLACK_PIN = [
    re.compile(r"(?:psf|ambv|python)/black[^\n]*\n(?:[^\n]*\n){0,3}?\s*rev:\s*['\"]?v?([0-9][\w.]*)"),
    re.compile(r"(?<![\w-])black(?:\[\w+\])?\s*==\s*([0-9][\w.]*)"),
]

FIELDS = [
    "full_name", "stars", "size_kb",
    "s1_first_author_date", "s2_first_committer_date", "s3_github_created", "s4_pypi_first_release", "pypi_name",
    "start_date", "start_source", "s1_s3_gap_days", "s1_s4_gap_days",
    "py_commits_total", "last_py_commit",
    "black_ever", "a1_black_first", "a1_sha", "black_end", "black_at_head", "black_reintroduced",
    "black_mechanisms_at_a1", "black_mechanisms_ever",
    "a3_black_pin", "a3_pin_release_date", "a1_before_pin_release",
    "a2_reformat_sha", "a2_reformat_date", "a2_reformat_py_files", "a2_reformat_subject",
    "line_length_a1", "skip_string_normalization_a1", "target_version_a1",
    "line_length_head", "skip_string_normalization_head",
    "ruff_format_first", "yapf_ever", "autopep8_ever", "isort_ever", "reorder_imports_ever",
    "black_in_first_commit",
    "py_commits_pre_24m", "py_commits_post_24m", "months_before_adoption", "months_after_adoption",
    "error",
]


NO_LAZY_FETCH = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}


def git(repo, *args, text=True, input=None):
    # Fail instead of downloading blobs one by one; fetch_blobs downloads them in batches.
    # Bytes in and out: text mode on Windows would turn "\n" in the input into "\r\n".
    out = subprocess.run(["git", "-C", repo, *args], capture_output=True, input=input.encode() if input else None,
                         check=True, env=NO_LAZY_FETCH).stdout
    return out.decode("utf-8", "replace") if text else out


def parse_date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def remove_clone(path):
    """Delete a clone; on Windows Git marks pack files read-only, which plain rmtree cannot delete."""
    def make_writable(func, target, _):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    if os.path.isdir(path):
        shutil.rmtree(path, onexc=make_writable)


def clone(full_name, clone_url):
    target = os.path.join(CLONE_DIR, full_name.replace("/", "__"))
    if not os.path.isdir(target):
        subprocess.run(["git", "clone", "--quiet", "--bare", "--filter=blob:none", clone_url, target], check=True)
    return target


def start_dates(repo):
    roots = git(repo, "rev-list", "--max-parents=0", "HEAD").split()
    author, committer = [], []
    for root in roots:
        a, c = git(repo, "log", "-1", "--format=%aI %cI", root).split()
        author.append(parse_date(a))
        committer.append(parse_date(c))
    return min(author), min(committer), roots


def pypi_first_release(full_name):
    """Return (name, first upload) for a PyPI project that links back to this repository."""
    repo_name = full_name.split("/")[1]
    for name in dict.fromkeys([repo_name, repo_name.lower(), repo_name.lower().replace("_", "-")]):
        try:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=30) as response:
                data = json.load(response)
        except Exception:
            continue
        info = data["info"]
        links = " ".join(filter(None, [info.get("home_page"), *(info.get("project_urls") or {}).values()])).lower()
        if f"github.com/{full_name.lower()}" not in links:
            continue
        uploads = [parse_date(f["upload_time_iso_8601"]) for files in data["releases"].values() for f in files]
        return name, min(uploads) if uploads else None
    return None, None


_black_releases = None


def black_release_date(version):
    global _black_releases
    if _black_releases is None:
        with urllib.request.urlopen("https://pypi.org/pypi/black/json", timeout=30) as response:
            releases = json.load(response)["releases"]
        _black_releases = {v: min(parse_date(f["upload_time_iso_8601"]) for f in files) for v, files in releases.items() if files}
    return _black_releases.get(version)


def mechanism_of(path):
    for mechanism, patterns in MECHANISMS.items():
        for pattern in patterns:
            if pattern.startswith("*") and pattern.strip("*").split("*")[0] in path:
                return mechanism
            if path == pattern or path.startswith(pattern + "/") or re.fullmatch(pattern.replace(".", r"\.").replace("*", ".*"), path):
                return mechanism
    return "other"


def config_history(repo):
    """Yield (sha, date, {path: new blob id or None}) for commits touching config files, oldest first."""
    out = git(repo, "log", "--reverse", "--first-parent", "--no-renames", "--raw", "--no-abbrev", "--format=%x00%H %cI", "HEAD", "--", *PATHS)
    for chunk in out.split("\x00")[1:]:
        lines = chunk.strip("\n").split("\n")
        sha, date = lines[0].split()
        changes = {}
        for line in lines[1:]:
            if line.startswith(":"):
                meta, path = line.split("\t", 1)
                new_blob, status = meta.split()[3], meta.split()[4]
                changes[path] = None if status.startswith("D") else new_blob
        yield sha, parse_date(date), changes


def fetch_blobs(repo, blobs):
    """Download the given blobs in one batch, then read them all."""
    blobs = sorted(set(blobs))
    if not blobs:
        return {}
    missing = git(repo, "cat-file", "--batch-check", input="\n".join(blobs) + "\n")
    wanted = [line.split()[0] for line in missing.splitlines() if line.endswith("missing")]
    # The same fetch a partial clone runs for one lazy blob, but for all of them at once.
    for start in range(0, len(wanted), 500):
        subprocess.run(["git", "-C", repo, "-c", "fetch.negotiationAlgorithm=noop", "fetch", "--quiet", "--no-tags", "--no-write-fetch-head",
                        "--recurse-submodules=no", "--filter=blob:none", "--stdin", "origin"],
                       input=("\n".join(wanted[start:start + 500]) + "\n").encode(), check=True, capture_output=True)
    proc = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    contents = {}
    for blob in blobs:
        proc.stdin.write(blob.encode() + b"\n")
        proc.stdin.flush()
        header = proc.stdout.readline().split()
        if header[-1] == b"missing":
            continue
        contents[blob] = proc.stdout.read(int(header[2])).decode("utf-8", "replace")
        proc.stdout.read(1)
    proc.stdin.close()
    proc.wait()
    return contents


def black_settings(text):
    try:
        black = tomllib.loads(text).get("tool", {}).get("black", {})
    except tomllib.TOMLDecodeError:
        return {}
    return black


def tool_timeline(repo):
    """Replay config history and return per-commit tool states."""
    history = list(config_history(repo))
    contents = fetch_blobs(repo, [blob for _, _, changes in history for blob in changes.values() if blob])
    state, timeline = {}, []
    for sha, date, changes in history:
        for path, blob in changes.items():
            if blob is None:
                state.pop(path, None)
            else:
                state[path] = contents.get(blob, "")
        tools = {name for name, pattern in TOOLS.items() if any(pattern.search(text) for text in state.values())}
        black_files = sorted(path for path, text in state.items() if TOOLS["black"].search(text))
        timeline.append({"sha": sha, "date": date, "tools": tools, "black_files": black_files, "state": dict(state)})
    return timeline


def py_commit_dates(repo):
    out = git(repo, "log", "--no-merges", "--format=%cI", "HEAD", "--", "*.py")
    return sorted(parse_date(line) for line in out.split())


def reformat_candidate(repo, around):
    since = (around - timedelta(days=180)).isoformat()
    until = (around + timedelta(days=365)).isoformat()
    out = git(repo, "log", "--no-merges", "--no-renames", f"--since={since}", f"--until={until}", "--format=%x00%H %cI %s", "--name-only", "HEAD", "--", "*.py")
    best = None
    for chunk in out.split("\x00")[1:]:
        lines = [line for line in chunk.strip("\n").split("\n") if line]
        sha, date, *subject = lines[0].split(" ")
        files = len(lines) - 1
        if best is None or files > best[2]:
            best = (sha, date, files, " ".join(subject))
    return best


def profile(candidate):
    row = {"full_name": candidate["full_name"], "stars": candidate["stars"], "size_kb": candidate["size_kb"],
           "s3_github_created": candidate["created_at"]}
    repo = clone(candidate["full_name"], candidate["clone_url"])

    s1, s2, roots = start_dates(repo)
    s3 = parse_date(candidate["created_at"])
    pypi_name, s4 = pypi_first_release(candidate["full_name"])
    plausible = datetime(1995, 1, 1, tzinfo=timezone.utc) <= s1 <= s3 + timedelta(days=30)
    start, source = (s1, "S1") if plausible else (s3, "S3")
    row.update({
        "s1_first_author_date": s1.isoformat(), "s2_first_committer_date": s2.isoformat(),
        "s4_pypi_first_release": s4.isoformat() if s4 else "", "pypi_name": pypi_name or "",
        "start_date": start.isoformat(), "start_source": source,
        "s1_s3_gap_days": (s3 - s1).days, "s1_s4_gap_days": (s4 - s1).days if s4 else "",
    })

    py_dates = py_commit_dates(repo)
    row["py_commits_total"] = len(py_dates)
    row["last_py_commit"] = py_dates[-1].isoformat() if py_dates else ""

    timeline = tool_timeline(repo)
    black_points = [t for t in timeline if "black" in t["tools"]]
    row["black_ever"] = bool(black_points)
    row["black_at_head"] = bool(timeline) and "black" in timeline[-1]["tools"]
    for tool in ("yapf", "autopep8", "isort", "reorder_imports"):
        row[f"{tool}_ever"] = any(tool in t["tools"] for t in timeline)
    ruff = next((t for t in timeline if "ruff_format" in t["tools"]), None)
    row["ruff_format_first"] = ruff["date"].isoformat() if ruff else ""
    if not black_points:
        return row

    first = black_points[0]
    a1 = first["date"]
    after = timeline[timeline.index(first):]
    end = next((t for t in after if "black" not in t["tools"]), None)
    reintroduced = end is not None and any("black" in t["tools"] for t in after[after.index(end):])
    row.update({
        "a1_black_first": a1.isoformat(), "a1_sha": first["sha"],
        "black_end": end["date"].isoformat() if end else "", "black_reintroduced": reintroduced,
        "black_mechanisms_at_a1": ";".join(sorted({mechanism_of(p) for p in first["black_files"]})),
        "black_mechanisms_ever": ";".join(sorted({mechanism_of(p) for t in black_points for p in t["black_files"]})),
        "black_in_first_commit": first["sha"] in roots,
    })

    pin = next((m.group(1) for text in first["state"].values() for pattern in BLACK_PIN for m in [pattern.search(text)] if m), None)
    release = black_release_date(pin) if pin else None
    row.update({"a3_black_pin": pin or "", "a3_pin_release_date": release.isoformat() if release else "",
                "a1_before_pin_release": bool(release and a1 < release)})

    for label, point in (("a1", first), ("head", timeline[-1])):
        settings = black_settings(point["state"].get("pyproject.toml", ""))
        row[f"line_length_{label}"] = settings.get("line-length", "")
        row[f"skip_string_normalization_{label}"] = settings.get("skip-string-normalization", "")
        if label == "a1":
            row["target_version_a1"] = ";".join(settings.get("target-version", []))

    best = reformat_candidate(repo, a1)
    if best:
        row.update({"a2_reformat_sha": best[0], "a2_reformat_date": best[1], "a2_reformat_py_files": best[2], "a2_reformat_subject": best[3][:120]})

    adoption = parse_date(best[1]) if best else a1
    black_stop = min(filter(None, [end["date"] if end else None, ruff["date"] if ruff else None, datetime.now(timezone.utc)]))
    row["py_commits_pre_24m"] = sum(adoption - WINDOW <= d < adoption for d in py_dates)
    row["py_commits_post_24m"] = sum(adoption < d <= adoption + WINDOW for d in py_dates)
    row["months_before_adoption"] = round((adoption - start).days / 30.44, 1)
    row["months_after_adoption"] = round((black_stop - adoption).days / 30.44, 1)
    return row


def main(candidates_csv, out_csv, limit=None):
    with open(candidates_csv, encoding="utf-8") as handle:
        candidates = list(csv.DictReader(handle))[: int(limit) if limit else None]
    done = set()
    if os.path.exists(out_csv):
        with open(out_csv, encoding="utf-8") as handle:
            done = {row["full_name"] for row in csv.DictReader(handle)}
    with open(out_csv, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, FIELDS)
        if not done:
            writer.writeheader()
        for candidate in candidates:
            if candidate["full_name"] in done:
                continue
            try:
                row = profile(candidate)
            except Exception as error:  # keep going; the error is recorded in the row
                row = {"full_name": candidate["full_name"], "error": f"{type(error).__name__}: {error}"[:300]}
            writer.writerow(row)
            handle.flush()
            print(candidate["full_name"], row.get("black_ever", ""), row.get("a1_black_first", ""), row.get("error", ""), file=sys.stderr, flush=True)
            if os.environ.get("KEEP_CLONES") != "1":
                remove_clone(os.path.join(CLONE_DIR, candidate["full_name"].replace("/", "__")))


if __name__ == "__main__":
    main(*sys.argv[1:])
