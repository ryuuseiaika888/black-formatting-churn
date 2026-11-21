"""Build the final sample: confirm adoption dates, match controls, collect churn.

Usage:
  python -m churn.sample adopters   # full clone + collect each adopter, confirm A2
  python -m churn.sample controls   # commit dates of control candidates
  python -m churn.sample match      # pair each adopter with one control
  python -m churn.sample collect-controls

Rules are in docs/protocol.md. Each step reads the previous step's CSV in data/
and skips repositories it already finished, so it can be stopped and rerun.
"""
import csv
import math
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

from . import collect
from .profile import CLONE_DIR, parse_date, py_commit_dates, remove_clone
from .summarize import is_adopter, is_control_candidate

WINDOW = timedelta(days=730)
A2_BEFORE, A2_AFTER = timedelta(days=180), timedelta(days=365)
A2_MIN_SHARE, A2_MIN_LINES = 0.9, 50
MIN_COMMITS = 30
WORKERS = int(os.environ.get("WORKERS", 4))

PROFILES = "data/profiles.csv"
CANDIDATES = "data/candidates.csv"
ADOPTERS = "data/adopters.csv"
CONTROL_ACTIVITY = "data/control_activity.csv"
MATCHES = "data/matches.csv"
CHURN_DIR = "data/churn"


csv.field_size_limit(10_000_000)  # control_activity.csv holds every commit date in one field


def read(path):
    with open(path, encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def append(path, fields, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields)
        if new:
            writer.writeheader()
        writer.writerow(row)


def done(path):
    return {row["full_name"] for row in read(path)} if os.path.exists(path) else set()


def clone_urls():
    return {row["full_name"]: row["clone_url"] for row in read(CANDIDATES)}


def churn_path(full_name):
    return os.path.join(CHURN_DIR, full_name.replace("/", "__") + ".csv")


def collect_window(full_name, url, since, until):
    """Full clone, collect churn for [since, until], delete the clone. Returns the rows."""
    path = churn_path(full_name)
    if not os.path.exists(path):
        repo = os.path.join(CLONE_DIR, "full", full_name.replace("/", "__"))
        if not os.path.isdir(repo):
            subprocess.run(["git", "clone", "--quiet", "--bare", url, repo], check=True)
        os.makedirs(CHURN_DIR, exist_ok=True)
        collect.main(repo, path, since.isoformat(), until.isoformat())
        remove_clone(repo)
    return read(path)


def py_commits_between(rows, start, end, exclude=None):
    return sum(start <= parse_date(r["commit_date"]) < end and r["hunks"] != "0" and r["sha"] != exclude for r in rows)


ADOPTER_FIELDS = [
    "full_name", "start_date", "a1", "black_stop", "a2_sha", "a2_date", "a2_formatting_lines", "a2_share",
    "a2_confirmed", "adoption_date", "pre_commits", "post_commits", "eligible", "reason",
]


def black_stop(profile):
    dates = [parse_date(v) for v in (profile["black_end"], profile["ruff_format_first"]) if v]
    a1 = parse_date(profile["a1_black_first"])
    later = [d for d in dates if d > a1]
    return min(later) if later else datetime.now(timezone.utc)


def adopters():
    urls, finished = clone_urls(), done(ADOPTERS)
    todo = [p for p in read(PROFILES) if not p["error"] and is_adopter(p) and p["full_name"] not in finished]
    with ProcessPoolExecutor(WORKERS) as pool:
        for row in pool.map(adopter_row, todo, [urls[p["full_name"]] for p in todo]):
            append(ADOPTERS, ADOPTER_FIELDS, row)
            print(row["full_name"], row["eligible"], row.get("a2_confirmed", ""), row["reason"], file=sys.stderr, flush=True)


def adopter_row(profile, url):
    name, a1 = profile["full_name"], parse_date(profile["a1_black_first"])
    row = {"full_name": name, "start_date": profile["start_date"], "a1": a1.isoformat()}
    try:
        rows = collect_window(name, url, a1 - A2_BEFORE - WINDOW, a1 + A2_AFTER + WINDOW)
        near = [r for r in rows if a1 - A2_BEFORE <= parse_date(r["commit_date"]) <= a1 + A2_AFTER]
        best = max(near, key=lambda r: int(r["formatting_lines"]), default=None)
        churn = int(best["added"]) + int(best["deleted"]) if best else 0
        share = int(best["formatting_lines"]) / churn if churn else 0
        confirmed = bool(best) and share >= A2_MIN_SHARE and int(best["formatting_lines"]) >= A2_MIN_LINES
        adoption = parse_date(best["commit_date"]) if confirmed else a1
        stop = black_stop(profile)
        pre = py_commits_between(rows, adoption - WINDOW, adoption)
        post = py_commits_between(rows, adoption, adoption + WINDOW, exclude=best["sha"] if confirmed else None)
        reasons = []
        if (adoption - parse_date(profile["start_date"])) < WINDOW:
            reasons.append("history before adoption < 24 months")
        if stop - adoption < WINDOW:
            reasons.append("Black used < 24 months after adoption")
        if pre < MIN_COMMITS or post < MIN_COMMITS:
            reasons.append(f"fewer than {MIN_COMMITS} commits in a window")
        row.update({
            "black_stop": stop.isoformat(), "a2_sha": best["sha"] if best else "",
            "a2_date": best["commit_date"] if best else "", "a2_formatting_lines": best["formatting_lines"] if best else 0,
            "a2_share": round(share, 3), "a2_confirmed": confirmed, "adoption_date": adoption.isoformat(),
            "pre_commits": pre, "post_commits": post, "eligible": not reasons, "reason": "; ".join(reasons),
        })
    except Exception as error:
        row.update({"eligible": False, "reason": f"error: {type(error).__name__}: {error}"[:300]})
    return row


def controls():
    urls, finished = clone_urls(), done(CONTROL_ACTIVITY)
    for profile in read(PROFILES):
        if profile["error"] or not is_control_candidate(profile) or profile["full_name"] in finished:
            continue
        name = profile["full_name"]
        repo = os.path.join(CLONE_DIR, name.replace("/", "__"))
        try:
            remove_clone(repo)
            subprocess.run(["git", "clone", "--quiet", "--bare", "--filter=blob:none", urls[name], repo], check=True)
            dates = " ".join(d.date().isoformat() for d in py_commit_dates(repo))
            error = ""
        except Exception as exc:
            dates, error = "", f"{type(exc).__name__}: {exc}"[:300]
        remove_clone(repo)
        append(CONTROL_ACTIVITY, ["full_name", "start_date", "py_commit_dates", "error"],
               {"full_name": name, "start_date": profile["start_date"], "py_commit_dates": dates, "error": error})
        print(name, len(dates.split()), error, file=sys.stderr, flush=True)


def match():
    """Greedy nearest-neighbour matching without replacement, adopters in order of adoption date."""
    pool = []
    for row in read(CONTROL_ACTIVITY):
        if not row["error"]:
            dates = [datetime.fromisoformat(d).replace(tzinfo=timezone.utc) for d in row["py_commit_dates"].split()]
            pool.append({"name": row["full_name"], "start": parse_date(row["start_date"]), "dates": dates})
    adopters_ = sorted((r for r in read(ADOPTERS) if r["eligible"] == "True"), key=lambda r: r["adoption_date"])
    used, out = set(), []
    for adopter in adopters_:
        date = parse_date(adopter["adoption_date"])
        age = (date - parse_date(adopter["start_date"])).days
        best = None
        for control in pool:
            if control["name"] in used or date - control["start"] < WINDOW:
                continue
            pre = sum(date - WINDOW <= d < date for d in control["dates"])
            post = sum(date <= d < date + WINDOW for d in control["dates"])
            if pre < MIN_COMMITS or post < MIN_COMMITS:
                continue
            distance = abs(math.log1p(pre) - math.log1p(int(adopter["pre_commits"]))) \
                + 0.5 * abs(math.log((date - control["start"]).days) - math.log(age))
            if best is None or distance < best[0]:
                best = (distance, control, pre, post)
        if best:
            used.add(best[1]["name"])
        out.append({
            "adopter": adopter["full_name"], "adoption_date": adopter["adoption_date"], "adopter_pre": adopter["pre_commits"],
            "control": best[1]["name"] if best else "", "control_pre": best[2] if best else "",
            "control_post": best[3] if best else "", "distance": round(best[0], 3) if best else "",
        })
    with open(MATCHES, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, list(out[0]))
        writer.writeheader()
        writer.writerows(out)
    print(f"{sum(bool(r['control']) for r in out)} of {len(out)} adopters matched", file=sys.stderr)


def control_churn(name, url, date):
    try:
        collect_window(name, url, date - WINDOW, date + WINDOW)
        return f"{name} ok"
    except Exception as error:
        return f"{name} error {error}"


def collect_controls():
    urls = clone_urls()
    todo = [r for r in read(MATCHES) if r["control"]]
    with ProcessPoolExecutor(WORKERS) as pool:
        for message in pool.map(control_churn, [r["control"] for r in todo], [urls[r["control"]] for r in todo],
                                [parse_date(r["adoption_date"]) for r in todo]):
            print(message, file=sys.stderr, flush=True)


if __name__ == "__main__":
    {"adopters": adopters, "controls": controls, "match": match, "collect-controls": collect_controls}[sys.argv[1]]()
