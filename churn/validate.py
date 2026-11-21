"""Manual validation of the hunk classifier.

Usage:
  python -m churn.validate sample   # draw hunks, write the labelling page
  python -m churn.validate score    # compare manual labels with the classifier

`sample` draws hunks from the analysis windows of randomly chosen repositories
(adopters and controls, before and after adoption), at most two per commit, until
each classifier category has its quota. The labelling page does not show the
classifier's answer; that is kept in data/validation/auto.csv. The labeller saves
data/validation/labels.csv from the page.
"""
import csv
import html
import json
import os
import random
import re
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

from .analyze import commits, load_sample, parse
from .classify import COMMENT, FORMATTING, LOGIC, UNPARSED, Hunk, classify_hunks
from .collect import HUNK_RE, INDEX_RE, NULL_BLOB, Blobs
from .profile import CLONE_DIR, remove_clone
from .sample import WORKERS, clone_urls, read

OUT = "data/validation"
QUOTA = {FORMATTING: 70, LOGIC: 70, COMMENT: 40, UNPARSED: 20}
REPOS_PER_GROUP = 15
PER_COMMIT, PER_REPO = 2, 12
MAX_LINES = 40
CONTEXT = 3
SEED = 2026


def file_hunks(repo, sha):
    """(path, old blob, new blob, hunks) for each modified .py file of one commit."""
    out = subprocess.run(["git", "-C", repo, "-c", "core.quotepath=off", "show", "--format=", "--full-index", "-M", "-U0",
                          "--no-color", "--no-ext-diff", "--diff-algorithm=histogram", sha, "--", "*.py"],
                         capture_output=True, check=True).stdout
    files, current, hunk = [], None, None
    for raw in out.splitlines(keepends=True):
        if raw.startswith(b"diff --git "):
            current, hunk = {"path": raw.split(b" b/")[-1].decode("utf-8", "replace").strip(), "old": b"", "new": b"", "hunks": []}, None
            files.append(current)
        elif hunk is None and raw.startswith(b"index ") and current is not None:
            match = INDEX_RE.match(raw)
            if match:
                current["old"], current["new"] = match.group(1), match.group(2)
        elif raw.startswith(b"@@ ") and current is not None:
            match = HUNK_RE.match(raw)
            hunk = Hunk(int(match.group(1)), int(match.group(2)) if match.group(2) is not None else 1, [])
            current["hunks"].append(hunk)
        elif hunk is not None and raw.startswith(b"+"):
            hunk.added.append(raw[1:].decode("utf-8", "surrogateescape"))
    return [f for f in files if f["hunks"] and not (NULL_BLOB.match(f["old"]) or NULL_BLOB.match(f["new"]))]


def view(old_lines, hunk):
    """Context, removed and added lines of one hunk, as (marker, text) pairs."""
    start = hunk.old_start - 1 if hunk.old_count > 0 else hunk.old_start
    end = start + hunk.old_count
    lines = [(" ", l) for l in old_lines[max(0, start - CONTEXT):start]]
    lines += [("-", l) for l in old_lines[start:end]]
    lines += [("+", l) for l in hunk.added]
    lines += [(" ", l) for l in old_lines[end:end + CONTEXT]]
    return [(m, l.rstrip("\r\n")) for m, l in lines]


def candidates(name, url, date):
    """Classified hunks from up to 40 random commits of one repository's window."""
    rng = random.Random(f"{SEED}:{name}")
    pool = [c for c in commits(name, date) if c["kind"] != "no_python_change"]
    rng.shuffle(pool)
    # Formatting and comment hunks are rare; take commits that contain them first.
    pool.sort(key=lambda c: not (c["f"] or c["c"]))
    repo = os.path.join(CLONE_DIR, "val", name.replace("/", "__"))
    out = []
    try:
        remove_clone(repo)
        subprocess.run(["git", "clone", "--quiet", "--bare", "--filter=blob:none", url, repo], check=True)
        blobs = Blobs(repo)
        for c in pool[:40]:
            hunks = []
            for f in file_hunks(repo, c["sha"]):
                old, new = blobs.read(f["old"]), blobs.read(f["new"])
                if old is None:
                    continue
                old_lines = old.splitlines(keepends=True)
                for hunk, category in zip(f["hunks"], classify_hunks(old, new, f["hunks"])):
                    lines = view(old_lines, hunk)
                    if hunk.old_count + len(hunk.added) <= MAX_LINES:
                        hunks.append({"name": name, "sha": c["sha"], "path": f["path"], "line": hunk.old_start,
                                      "period": "pre" if c["offset"] < 0 else "post", "auto": category, "lines": lines})
            rng.shuffle(hunks)
            out.extend(hunks[:PER_COMMIT * 3])
        blobs.close()
    finally:
        remove_clone(repo)
    return out


def sample():
    rng = random.Random(SEED)
    adopters, pairs = load_sample()
    urls = clone_urls()
    chosen = rng.sample(pairs, REPOS_PER_GROUP * 2)
    repos = [(p["adopter"], p) for p in chosen[:REPOS_PER_GROUP]] + [(p["control"], p) for p in chosen[REPOS_PER_GROUP:]]
    with ProcessPoolExecutor(WORKERS) as pool:
        pools = list(pool.map(candidates, [n for n, _ in repos], [urls[n] for n, _ in repos],
                              [parse(adopters[p["adopter"]]["adoption_date"]) for _, p in repos]))
    for (name, _), found in zip(repos, pools):
        print(name, dict(Counter(h["auto"] for h in found)), file=sys.stderr)

    # Round-robin over repositories so that no single project dominates a category.
    picked, per_commit, per_repo, have = [], Counter(), Counter(), Counter()
    progress = True
    while progress and any(have[c] < q for c, q in QUOTA.items()):
        progress = False
        for pool in pools:
            for i, h in enumerate(pool):
                if have[h["auto"]] < QUOTA[h["auto"]] and per_commit[h["sha"]] < PER_COMMIT and per_repo[h["name"]] < PER_REPO:
                    picked.append(pool.pop(i))
                    have[h["auto"]] += 1
                    per_commit[h["sha"]] += 1
                    per_repo[h["name"]] += 1
                    progress = True
                    break
    rng.shuffle(picked)
    for i, h in enumerate(picked, 1):
        h["id"] = f"H{i:03d}"

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "auto.csv"), "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, ["id", "name", "sha", "path", "line", "period", "auto"])
        writer.writeheader()
        writer.writerows({k: h[k] for k in writer.fieldnames} for h in picked)
    with open(os.path.join(OUT, "label.html"), "w", encoding="utf-8") as handle:
        handle.write(page(picked))
    print(f"{len(picked)} hunks: {dict(have)}", file=sys.stderr)


def page(hunks):
    items = []
    for h in hunks:
        code = "".join(f'<span class="{ {"+": "add", "-": "del", " ": "ctx"}[m]}">{m} {html.escape(l)}</span>\n' for m, l in h["lines"])
        items.append(f'<section id="{h["id"]}"><h2>{h["id"]} <small>{html.escape(h["name"])} · {h["sha"][:10]} · '
                     f'{html.escape(h["path"])}:{h["line"]}</small></h2><pre>{code}</pre><div class="choices" data-id="{h["id"]}"></div></section>')
    return TEMPLATE.replace("{{ITEMS}}", "\n".join(items)).replace("{{IDS}}", json.dumps([h["id"] for h in hunks]))


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hunk labelling</title>
<style>
body{font:14px system-ui,sans-serif;max-width:980px;margin:0 auto;padding:16px;background:#fff;color:#222}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:8px 0;z-index:1}
section{border-bottom:1px solid #eee;padding:12px 0}
h2{font-size:15px;margin:0 0 6px} small{color:#777;font-weight:normal}
pre{background:#f7f7f7;padding:8px;overflow-x:auto;font:12px/1.45 Consolas,monospace;margin:0 0 8px}
.add{background:#e6ffec;display:block}.del{background:#ffebe9;display:block}.ctx{color:#888;display:block}
.choices label{margin-right:14px;cursor:pointer} .choices input[type=text]{width:320px}
details{margin:6px 0 10px} button{font-size:14px;padding:4px 12px}
</style></head><body>
<header><b>Hunk labelling</b> · <span id="count"></span> labelled ·
<button onclick="download()">Save labels.csv</button>
<details><summary>Definitions (read first)</summary>
<p>Judge only the shown hunk on its own (red lines replaced by green lines; grey is unchanged context).</p>
<ul>
<li><b>formatting</b>: the code means exactly the same and only its layout changes: whitespace, indentation of continuation lines, line breaks, parentheses that only wrap lines, quote style, trailing commas, blank lines, re-indented docstring lines.</li>
<li><b>comment</b>: a <code>#</code> comment is added, removed or reworded (and nothing else but layout changes).</li>
<li><b>logic</b>: anything else: code, string contents, docstring wording, type hints, imports added, removed or reordered. (Dropping a <code>u</code> string prefix counts as formatting.)</li>
<li><b>cannot tell</b>: the hunk alone is not enough to decide (for example it opens a bracket that a different hunk closes).</li>
</ul>
<p>Tick <i>cosmetic</i> for a logic hunk that would not change behaviour in practice (for example import reordering, <code>%</code> to f-string with the same output).</p>
</details></header>
{{ITEMS}}
<script>
const ids = {{IDS}}, key = "hunk-labels";
let state = {};
try { state = JSON.parse(localStorage.getItem(key) || "{}"); } catch (e) {}
function save() { try { localStorage.setItem(key, JSON.stringify(state)); } catch (e) {} count(); }
function count() { document.getElementById("count").textContent = ids.filter(i => state[i] && state[i].label).length + " / " + ids.length; }
for (const box of document.querySelectorAll(".choices")) {
  const id = box.dataset.id, s = state[id] || (state[id] = {label: "", cosmetic: false, note: ""});
  for (const l of ["formatting", "comment", "logic", "cannot tell"]) {
    const lab = document.createElement("label"), r = document.createElement("input");
    r.type = "radio"; r.name = id; r.checked = s.label === l;
    r.onchange = () => { s.label = l; save(); };
    lab.append(r, " " + l); box.append(lab);
  }
  const lab = document.createElement("label"), c = document.createElement("input");
  c.type = "checkbox"; c.checked = s.cosmetic; c.onchange = () => { s.cosmetic = c.checked; save(); };
  lab.append(c, " cosmetic"); box.append(lab);
  const n = document.createElement("input"); n.type = "text"; n.placeholder = "note (optional)"; n.value = s.note;
  n.oninput = () => { s.note = n.value; save(); }; box.append(n);
}
count();
function download() {
  const q = v => '"' + String(v).replace(/"/g, '""') + '"';
  const rows = ["id,label,cosmetic,note"].concat(ids.map(i => [i, state[i].label, state[i].cosmetic, state[i].note].map(q).join(",")));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([rows.join("\\n") + "\\n"], {type: "text/csv"}));
  a.download = "labels.csv"; a.click();
}
</script></body></html>
"""


def score():
    auto = {r["id"]: r for r in read(os.path.join(OUT, "auto.csv"))}
    labels = {r["id"]: r for r in read(os.path.join(OUT, "labels.csv")) if r["label"]}
    manual_names = ["formatting", "comment", "logic", "cannot tell"]
    confusion = defaultdict(Counter)
    for i, r in labels.items():
        confusion[auto[i]["auto"]][r["label"]] += 1
    lines = ["# Classifier validation", "", f"{len(labels)} of {len(auto)} hunks labelled.", "",
             "Rows: classifier. Columns: manual label.", "",
             "| Classifier | " + " | ".join(manual_names) + " | Precision |", "|---" * (len(manual_names) + 2) + "|"]
    for category in (FORMATTING, COMMENT, LOGIC, UNPARSED):
        row = confusion[category]
        total = sum(row.values())
        precision = f"{row[category] / total:.2f}" if category != UNPARSED and total else "-"
        lines.append(f"| {category} | " + " | ".join(str(row[m]) for m in manual_names) + f" | {precision} |")
    cosmetic = sum(r["cosmetic"] == "true" and r["label"] == "logic" for r in labels.values())
    lines += ["", f"Manual logic hunks marked cosmetic: {cosmetic} of {sum(r['label'] == 'logic' for r in labels.values())}."]
    with open("results/validation.md", "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    {"sample": sample, "score": score}[sys.argv[1]]()
