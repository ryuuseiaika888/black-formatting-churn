"""Descriptive statistics of Black usage and the date cross-checks, as Markdown.

Usage: python -m churn.summarize PROFILES_CSV OUT_MD
"""
import csv
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone


def date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc) if value else None


def flag(value):
    return value == "True"


def num(value):
    return float(value) if value not in ("", None) else None


def pct(part, whole):
    return f"{part} ({100 * part / whole:.0f}%)" if whole else "0"


def quartiles(values):
    values = sorted(v for v in values if v is not None)
    if len(values) < 4:
        return "n/a" if not values else ", ".join(f"{v:g}" for v in values)
    q1, q2, q3 = statistics.quantiles(values, n=4)
    return f"median {q2:.0f} (IQR {q1:.0f} to {q3:.0f}), n={len(values)}"


def is_adopter(row):
    return (flag(row["black_ever"]) and not flag(row["black_in_first_commit"])
            and (num(row["months_before_adoption"]) or 0) >= 24 and (num(row["months_after_adoption"]) or 0) >= 24
            and (num(row["py_commits_pre_24m"]) or 0) >= 30 and (num(row["py_commits_post_24m"]) or 0) >= 30)


def is_control_candidate(row):
    return (not flag(row["black_ever"]) and not row["ruff_format_first"]
            and not flag(row["yapf_ever"]) and not flag(row["autopep8_ever"])
            and (num(row["py_commits_total"]) or 0) >= 60)


def summarize(rows):
    ok = [r for r in rows if not r["error"]]
    black = [r for r in ok if flag(r["black_ever"])]
    lines = ["# Black usage in the profiled candidates", ""]
    lines += [f"Profiled: {len(rows)}; errors: {len(rows) - len(ok)}; analysed: {len(ok)}.", ""]

    lines += ["## Adoption", "", "| Measure | Value |", "|---|---|"]
    lines.append(f"| Black referenced at some point | {pct(len(black), len(ok))} |")
    lines.append(f"| Black referenced at HEAD | {pct(sum(flag(r['black_at_head']) for r in ok), len(ok))} |")
    replaced = [r for r in black if r["ruff_format_first"] and date(r["ruff_format_first"]) >= date(r["a1_black_first"])]
    lines.append(f"| Black users that later added `ruff format` | {pct(len(replaced), len(black))} |")
    lines.append(f"| Black removed, then reintroduced | {pct(sum(flag(r['black_reintroduced']) for r in black), len(black))} |")
    lines.append(f"| `ruff format` without Black ever | {pct(sum(bool(r['ruff_format_first']) and not flag(r['black_ever']) for r in ok), len(ok))} |")
    for tool in ("isort", "reorder_imports", "yapf", "autopep8"):
        lines.append(f"| {tool} referenced, among Black users | {pct(sum(flag(r[tool + '_ever']) for r in black), len(black))} |")
    lines.append("")

    lines += ["### First Black reference (A1) by year", "", "| Year | Repositories |", "|---|---|"]
    for year, count in sorted(Counter(date(r["a1_black_first"]).year for r in black).items()):
        lines.append(f"| {year} | {count} |")
    lines.append("")

    lines += ["### How Black is wired in at A1", "", "| Mechanism | At A1 | Ever |", "|---|---|---|"]
    at_a1 = Counter(m for r in black for m in r["black_mechanisms_at_a1"].split(";") if m)
    ever = Counter(m for r in black for m in r["black_mechanisms_ever"].split(";") if m)
    for mechanism in sorted(ever, key=ever.get, reverse=True):
        lines.append(f"| {mechanism} | {pct(at_a1[mechanism], len(black))} | {pct(ever[mechanism], len(black))} |")
    lines.append("")

    lines += ["### Settings in `pyproject.toml` at A1", "", "| Setting | Repositories |", "|---|---|"]
    lengths = Counter(r["line_length_a1"] for r in black if r["line_length_a1"])
    lines.append(f"| `line-length` set | {pct(sum(lengths.values()), len(black))}; values: {', '.join(f'{k}: {v}' for k, v in lengths.most_common(5))} |")
    lines.append(f"| `skip-string-normalization = true` | {pct(sum(r['skip_string_normalization_a1'] == 'True' for r in black), len(black))} |")
    lines.append("")

    lines += ["## Cross-checks", "", "### Project start date", "", "| Check | Value |", "|---|---|"]
    lines.append(f"| Start date taken from S1 (first commit) | {pct(sum(r['start_source'] == 'S1' for r in ok), len(ok))} |")
    lines.append(f"| S1 more than 365 days before GitHub creation (S3) | {pct(sum((num(r['s1_s3_gap_days']) or 0) > 365 for r in ok), len(ok))} |")
    with_pypi = [r for r in ok if r["s4_pypi_first_release"]]
    lines.append(f"| PyPI project linked to the repository (S4 available) | {pct(len(with_pypi), len(ok))} |")
    lines.append(f"| S4 more than 365 days away from S1 | {pct(sum(abs(num(r['s1_s4_gap_days'])) > 365 for r in with_pypi), len(with_pypi))} |")
    lines.append(f"| Days from S1 to S3 | {quartiles(num(r['s1_s3_gap_days']) for r in ok)} |")
    lines.append("")

    lines += ["### Black adoption date", "", "| Check | Value |", "|---|---|"]
    gaps = [(date(r["a2_reformat_date"]) - date(r["a1_black_first"])).days for r in black if r["a2_reformat_date"]]
    lines.append(f"| Days from A1 (config) to A2 (largest `.py` commit near A1) | {quartiles(gaps)} |")
    lines.append(f"| A2 within 30 days of A1 | {pct(sum(abs(g) <= 30 for g in gaps), len(gaps))} |")
    pinned = [r for r in black if r["a3_pin_release_date"]]
    lines.append(f"| Black version pinned at A1 | {pct(len(pinned), len(black))} |")
    lines.append(f"| A1 earlier than the pinned version's release (error) | {pct(sum(flag(r['a1_before_pin_release']) for r in pinned), len(pinned))} |")
    lag = [(date(r["a1_black_first"]) - date(r["a3_pin_release_date"])).days for r in pinned]
    lines.append(f"| Days from pinned release to A1 | {quartiles(lag)} |")
    lines.append(f"| A1 before Black's first release (error) | {sum(date(r['a1_black_first']) < datetime(2018, 3, 14, tzinfo=timezone.utc) for r in black)} |")
    lines.append(f"| Months from project start to adoption | {quartiles(num(r['months_before_adoption']) for r in black)} |")
    lines.append("")

    adopters = [r for r in ok if is_adopter(r)]
    controls = [r for r in ok if is_control_candidate(r)]
    lines += ["## Eligibility (docs/protocol.md)", "", "| Group | Repositories |", "|---|---|"]
    lines.append(f"| Adopters meeting all rules | {len(adopters)} |")
    lines.append(f"| Control candidates before matching | {len(controls)} |")
    lines.append("")
    return "\n".join(lines)


def main(profiles_csv, out_md):
    with open(profiles_csv, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    with open(out_md, "w", encoding="utf-8") as handle:
        handle.write(summarize(rows))


if __name__ == "__main__":
    main(*sys.argv[1:])
