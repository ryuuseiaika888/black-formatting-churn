"""Statistics and figures for RQ1-RQ3.

Usage: python -m churn.analyze

Reads data/adopters.csv, data/matches.csv, data/exclusions.csv and data/churn/*.csv.
Writes results/analysis.md, results/repo_measures.csv, results/monthly.csv and
figures to results/figures/ (PDF for the paper, PNG for quick viewing).
"""
import csv
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats

from .sample import ADOPTERS, MATCHES, WINDOW, churn_path, read

EXCLUSIONS = "data/exclusions.csv"
MESSAGES = "data/formatting_only_messages.csv"  # written by churn.messages
OUT = "results"
FIGURES = os.path.join(OUT, "figures")
SEED = 2026
MONTH = 30.4375
EARLY = timedelta(days=30)  # sensitivity: first month after adoption left out
# Black releases that changed the stable style (first release of each yearly style), from PyPI.
STYLE_RELEASES = {"22.1.0": "2022-01-29", "23.1.0": "2023-02-01", "24.1.0": "2024-01-26", "25.1.0": "2025-01-29"}


def parse(value):
    date = datetime.fromisoformat(value)
    return date.astimezone(timezone.utc) if date.tzinfo else date.replace(tzinfo=timezone.utc)


def load_sample():
    excluded = {r["full_name"] for r in read(EXCLUSIONS)} if os.path.exists(EXCLUSIONS) else set()
    adopters = {r["full_name"]: r for r in read(ADOPTERS) if r["eligible"] == "True" and r["full_name"] not in excluded}
    pairs = [r for r in read(MATCHES) if r["adopter"] in adopters and r["control"] and r["control"] not in excluded]
    return adopters, pairs


def commits(name, date, skip_sha=None):
    """Commits that change .py files, with their offset in days from the (pseudo-)adoption date."""
    out = []
    for row in read(churn_path(name)):
        if row["kind"] in ("no_python_change",) or row["sha"] == skip_sha:
            continue
        offset = (parse(row["commit_date"]) - date).total_seconds() / 86400
        if -WINDOW.days <= offset < WINDOW.days:
            out.append({
                "sha": row["sha"], "offset": offset, "kind": row["kind"],
                "f": int(row["formatting_lines"]), "c": int(row["comment_lines"]), "l": int(row["logic_lines"]),
                "date": parse(row["commit_date"]),
            })
    return out


def measures(rows):
    f = sum(r["f"] for r in rows)
    classified = f + sum(r["c"] + r["l"] for r in rows)
    n = len(rows)
    # Formatting that rides along in commits that also change code or comments.
    mixed = [r for r in rows if r["kind"] != "formatting_only"]
    inc_f = sum(r["f"] for r in mixed)
    inc_total = inc_f + sum(r["c"] + r["l"] for r in mixed)
    return {
        "commits": n,
        "formatting_lines": f,
        "classified_lines": classified,
        "fmt_share": f / classified if classified else math.nan,
        "fmt_per_commit": f / n if n else math.nan,
        "fmt_only_rate": 100 * sum(r["kind"] == "formatting_only" for r in rows) / n if n else math.nan,
        "fmt_touch_rate": 100 * sum(r["f"] > 0 for r in rows) / n if n else math.nan,
        "inc_share": inc_f / inc_total if inc_total else math.nan,
    }


def periods(rows, early=timedelta(0)):
    pre = [r for r in rows if r["offset"] < 0]
    post = [r for r in rows if r["offset"] >= early.days]
    year1 = [r for r in post if r["offset"] < 365.25]
    year2 = [r for r in post if r["offset"] >= 365.25]
    return {"pre": measures(pre), "post": measures(post), "y1": measures(year1), "y2": measures(year2)}


def repo_table(adopters, pairs, early=timedelta(0)):
    table = []
    for pair in pairs:
        adopter = adopters[pair["adopter"]]
        date = parse(adopter["adoption_date"])
        skip = adopter["a2_sha"] if adopter["a2_confirmed"] == "True" else None
        for group, name in (("adopter", pair["adopter"]), ("control", pair["control"])):
            rows = commits(name, date, skip if group == "adopter" else None)
            table.append({"group": group, "name": name, "pair": pair["adopter"], "confirmed": adopter["a2_confirmed"] == "True",
                          "rows": rows, **{f"{p}_{k}": v for p, m in periods(rows, early).items() for k, v in m.items()}})
    return table


# --- statistics -------------------------------------------------------------

def wilcoxon(x, y):
    """Paired Wilcoxon signed-rank test, matched-pairs rank-biserial r and bootstrap CI of the median difference."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    keep = ~(np.isnan(x) | np.isnan(y))
    d = (y - x)[keep]
    nonzero = d[d != 0]
    ranks = stats.rankdata(np.abs(nonzero))
    r = (ranks[nonzero > 0].sum() - ranks[nonzero < 0].sum()) / ranks.sum() if len(nonzero) else 0.0
    p = stats.wilcoxon(d).pvalue if len(nonzero) else 1.0
    return {"n": int(keep.sum()), "median_x": float(np.median(x[keep])), "median_y": float(np.median(y[keep])),
            "median_diff": float(np.median(d)), "ci": bootstrap_median(d), "p": float(p), "effect": float(r),
            "effect_name": "r_rb", "decreased": int((d < 0).sum())}


def mann_whitney(x, y):
    """Mann-Whitney U test and Cliff's delta (positive: y tends to be larger than x)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    x, y = x[~np.isnan(x)], y[~np.isnan(y)]
    result = stats.mannwhitneyu(y, x, alternative="two-sided")
    delta = 2 * result.statistic / (len(x) * len(y)) - 1
    return {"n": f"{len(x)}/{len(y)}", "median_x": float(np.median(x)), "median_y": float(np.median(y)),
            "median_diff": float(np.median(y) - np.median(x)), "ci": (math.nan, math.nan), "p": float(result.pvalue),
            "effect": float(delta), "effect_name": "Cliff's d", "decreased": ""}


def bootstrap_median(d, n=10_000):
    if len(d) == 0:
        return (math.nan, math.nan)
    rng = np.random.default_rng(SEED)
    medians = np.median(rng.choice(d, (n, len(d))), axis=1)
    return (float(np.percentile(medians, 2.5)), float(np.percentile(medians, 97.5)))


def holm(pvalues):
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted, running = [0.0] * len(pvalues), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvalues) - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def magnitude(effect, name):
    """Romano et al. thresholds for Cliff's delta; the same cut-offs are used for the rank-biserial r."""
    a = abs(effect)
    return "negligible" if a < 0.147 else "small" if a < 0.33 else "medium" if a < 0.474 else "large"


def column(table, group, key, confirmed_only=False):
    return [r[key] for r in table if r["group"] == group and (r["confirmed"] or not confirmed_only)]


def paired_change(table, key, after):
    """Per pair: (post - pre) of the adopter and of its control."""
    by = {(r["pair"], r["group"]): r for r in table}
    pairs = sorted({r["pair"] for r in table})
    adopter = [by[(p, "adopter")][f"{after}_{key}"] - by[(p, "adopter")][f"pre_{key}"] for p in pairs]
    control = [by[(p, "control")][f"{after}_{key}"] - by[(p, "control")][f"pre_{key}"] for p in pairs]
    return control, adopter


def tests(table, confirmed_only=False):
    """The pre-registered family: H1, H2, H3 within adopters, and the matched difference-in-differences."""
    sub = [r for r in table if r["confirmed"] or not confirmed_only]
    out = []
    for key, label, hyp in (("fmt_share", "Formatting share of changed lines", "H1"),
                            ("fmt_only_rate", "Formatting-only commits per 100 commits", "H2")):
        out.append((hyp, f"{label}: adopters pre vs post", wilcoxon(column(sub, "adopter", f"pre_{key}"), column(sub, "adopter", f"post_{key}"))))
        out.append((hyp, f"{label}: change in adopters vs matched controls", wilcoxon(*paired_change(sub, key, "post"))))
        out.append((hyp, f"{label}: controls pre vs post", wilcoxon(column(sub, "control", f"pre_{key}"), column(sub, "control", f"post_{key}"))))
    out.append(("H3", "Formatting share: adopters pre vs second year after", wilcoxon(column(sub, "adopter", "pre_fmt_share"), column(sub, "adopter", "y2_fmt_share"))))
    out.append(("H3", "Formatting share: adopters first vs second year after", wilcoxon(column(sub, "adopter", "y1_fmt_share"), column(sub, "adopter", "y2_fmt_share"))))
    out.append(("H3", "Formatting share, second year: change in adopters vs matched controls", wilcoxon(*paired_change(sub, "fmt_share", "y2"))))
    adjusted = holm([t[2]["p"] for t in out])
    for t, p in zip(out, adjusted):
        t[2]["p_holm"] = p
    return out


def exploratory(table):
    """Not pre-registered: formatting inside commits that also change code or comments."""
    out = []
    for after, name in (("post", "24 months"), ("y2", "second year")):
        out.append(("E1", f"Incidental formatting share, {name}: adopters pre vs after",
                    wilcoxon(column(table, "adopter", "pre_inc_share"), column(table, "adopter", f"{after}_inc_share"))))
        out.append(("E1", f"Incidental formatting share, {name}: change in adopters vs matched controls",
                    wilcoxon(*paired_change(table, "inc_share", after))))
    for t, p in zip(out, holm([t[2]["p"] for t in out])):
        t[2]["p_holm"] = p
    return out


def between_groups(table):
    """Unpaired comparisons asked for in the study plan (Mann-Whitney U)."""
    out = []
    for key, label in (("fmt_share", "Formatting share"), ("fmt_only_rate", "Formatting-only commits per 100")):
        for period in ("pre", "post"):
            out.append((f"{label}, {period}: controls vs adopters", mann_whitney(column(table, "control", f"{period}_{key}"), column(table, "adopter", f"{period}_{key}"))))
    return out


# --- monthly series and style releases --------------------------------------

def monthly(table):
    rows = []
    for repo in table:
        months = {}
        for c in repo["rows"]:
            m = math.floor(c["offset"] / MONTH)
            acc = months.setdefault(m, [0, 0, 0, 0])
            acc[0] += c["f"]
            acc[1] += c["f"] + c["c"] + c["l"]
            acc[2] += 1
            acc[3] += c["kind"] == "formatting_only"
        for m in range(-24, 24):
            f, total, n, only = months.get(m, [0, 0, 0, 0])
            rows.append({"group": repo["group"], "name": repo["name"], "month": m, "formatting_lines": f,
                         "classified_lines": total, "commits": n, "formatting_only": only})
    return rows


def style_release_share(table):
    """Share of post-adoption formatting lines that fall within 90 days after a Black stable-style release."""
    near = total = 0
    for repo in table:
        if repo["group"] != "adopter":
            continue
        for c in repo["rows"]:
            if c["offset"] < 0:
                continue
            total += c["f"]
            if any(timedelta(0) <= c["date"] - parse(d) < timedelta(days=90) for d in STYLE_RELEASES.values()):
                near += c["f"]
    days = sum(90 for _ in STYLE_RELEASES)
    return near, total, days


# --- output ------------------------------------------------------------------

def fmt(x, digits=3):
    if isinstance(x, str):
        return x
    if isinstance(x, float) and math.isnan(x):
        return "-"
    return f"{x:.{digits}g}"


def quartiles(values):
    q = np.percentile(values, [25, 50, 75])
    return f"{round(q[1])} (IQR {round(q[0])} to {round(q[2])})"


def sample_description(adopters, pairs):
    rows = [adopters[p["adopter"]] for p in pairs]
    confirmed = [r for r in rows if r["a2_confirmed"] == "True"]
    lag = [(parse(r["a2_date"]) - parse(r["a1"])).total_seconds() / 86400 for r in confirmed]
    years = Counter(parse(r["adoption_date"]).year for r in rows)
    lines = ["| Measure | Value |", "|---|---|",
             f"| Adopter-control pairs | {len(pairs)} |",
             f"| Reformat commit confirmed (A2) | {len(confirmed)} ({100 * len(confirmed) / len(rows):.0f}%) |",
             f"| Days from A1 to confirmed A2 | {quartiles(lag)} |",
             f"| Confirmed A2 within 30 days of A1 | {sum(abs(d) <= 30 for d in lag)} of {len(lag)} |",
             f"| Lines changed by the confirmed reformat commit | {quartiles([int(r['a2_formatting_lines']) for r in confirmed])} |",
             f"| Formatting share of the best A2 candidate, unconfirmed adopters | "
             f"{', '.join(f'{float(r['a2_share']):.2f}' for r in rows if r['a2_confirmed'] != 'True')} |",
             f"| Project age at adoption, months | {quartiles([(parse(r['adoption_date']) - parse(r['start_date'])).days / MONTH for r in rows])} |",
             f"| Adopter `.py` commits, 24 months before | {quartiles([int(r['pre_commits']) for r in rows])} |",
             f"| Control `.py` commits, 24 months before | {quartiles([int(p['control_pre']) for p in pairs])} |",
             f"| Matching distance | {quartiles([float(p['distance']) * 100 for p in pairs])} (x100) |",
             f"| Adoption year | {', '.join(f'{y}: {n}' for y, n in sorted(years.items()))} |"]
    return lines


def message_table():
    """Formatting-only commits by what their message says (see churn/messages.py for the patterns)."""
    rows = read(MESSAGES)
    names = ["black", "bot", "other tool", "style", "other"]
    lines = ["| Group | Period | Commits | " + " | ".join(names) + " |", "|---" * (len(names) + 3) + "|"]
    for group in ("adopter", "control"):
        for period in ("pre", "post"):
            sub = [r for r in rows if r["group"] == group and r["period"] == period]
            counts = Counter(r["category"] for r in sub)
            lines.append(f"| {group} | {period} | {len(sub)} | " + " | ".join(f"{counts[n]} ({100 * counts[n] / len(sub):.0f}%)" for n in names) + " |")
    lines.append("")
    by_lines = Counter()
    for r in rows:
        by_lines[(r["group"], r["period"], r["category"] == "black")] += int(r["formatting_lines"])
    lines.append(f"Formatting lines in adopters' post-adoption formatting-only commits that mention Black: "
                 f"{by_lines[('adopter', 'post', True)]:,} of {by_lines[('adopter', 'post', True)] + by_lines[('adopter', 'post', False)]:,}.")
    return lines


def test_table(results):
    lines = ["| Hyp. | Comparison | n | Median before / x | Median after / y | Median diff. [95% CI] | p | p (Holm) | Effect | Size |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for hyp, label, r in results:
        ci = f"{fmt(r['median_diff'], 4)} [{fmt(r['ci'][0], 4)}, {fmt(r['ci'][1], 4)}]"
        lines.append(f"| {hyp} | {label} | {r['n']} | {fmt(r['median_x'], 4)} | {fmt(r['median_y'], 4)} | {ci} | "
                     f"{r['p']:.2g} | {r.get('p_holm', math.nan):.2g} | {r['effect_name']} = {r['effect']:.2f} | {magnitude(r['effect'], r['effect_name'])} |")
    return lines


def describe(table):
    lines = ["| Group | Period | Commits (median) | Formatting lines (sum) | Classified lines (sum) | Formatting share (median) | Formatting-only per 100 (median) | Commits touching formatting per 100 (median) | Incidental formatting share (median) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for group in ("adopter", "control"):
        for period in ("pre", "post", "y1", "y2"):
            sub = [r for r in table if r["group"] == group]
            lines.append(f"| {group} | {period} | {np.median([r[f'{period}_commits'] for r in sub]):.0f} | "
                         f"{sum(r[f'{period}_formatting_lines'] for r in sub):,} | {sum(r[f'{period}_classified_lines'] for r in sub):,} | "
                         f"{np.nanmedian([r[f'{period}_fmt_share'] for r in sub]):.4f} | {np.nanmedian([r[f'{period}_fmt_only_rate'] for r in sub]):.2f} | "
                         f"{np.nanmedian([r[f'{period}_fmt_touch_rate'] for r in sub]):.1f} | {np.nanmedian([r[f'{period}_inc_share'] for r in sub]):.4f} |")
    return lines


def figures(table, series):
    os.makedirs(FIGURES, exist_ok=True)
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    colors = {"adopter": "#1f6fb4", "control": "#d9822b"}

    # Monthly formatting share, median and interquartile range over repositories.
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    for group in ("adopter", "control"):
        xs, med, lo, hi = [], [], [], []
        for m in range(-24, 24):
            values = [r["formatting_lines"] / r["classified_lines"] for r in series
                      if r["group"] == group and r["month"] == m and r["classified_lines"]]
            xs.append(m)
            med.append(np.median(values))
            lo.append(np.percentile(values, 25))
            hi.append(np.percentile(values, 75))
        ax.plot(xs, med, color=colors[group], label=f"{group}s (median)")
        ax.fill_between(xs, lo, hi, color=colors[group], alpha=0.15, linewidth=0)
    ax.axvline(0, color="grey", linestyle="--", linewidth=0.8)
    ax.set_xlabel("Months relative to (pseudo-)adoption")
    ax.set_ylabel("Formatting share of changed lines")
    ax.legend(frameon=False)
    save(fig, "monthly_share")

    # Pre vs post per repository (slope chart), adopters and controls side by side.
    fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.8), sharey=True)
    for ax, group in zip(axes, ("adopter", "control")):
        for r in table:
            if r["group"] == group:
                ax.plot([0, 1], [r["pre_fmt_share"] * 100, r["post_fmt_share"] * 100], color=colors[group], alpha=0.35, linewidth=0.8, marker="o", markersize=2)
        ax.set_xticks([0, 1], ["24 months before", "24 months after"])
        ax.set_xlim(-0.2, 1.2)
        ax.set_title(f"{group.capitalize()}s", fontsize=9)
        ax.set_yscale("symlog", linthresh=0.5)
    axes[0].set_ylabel("Formatting share of changed lines (%)")
    save(fig, "pre_post_share")

    # Formatting-only commits per 100 commits, box plots per group and period.
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    data, labels, boxcolors = [], [], []
    for group in ("adopter", "control"):
        for period, name in (("pre", "before"), ("y1", "year 1 after"), ("y2", "year 2 after")):
            data.append([v for v in column(table, group, f"{period}_fmt_only_rate") if not math.isnan(v)])
            labels.append(f"{group}s\n{name}")
            boxcolors.append(colors[group])
    parts = ax.boxplot(data, patch_artist=True, showfliers=True, flierprops={"markersize": 2}, medianprops={"color": "black"})
    for patch, color in zip(parts["boxes"], boxcolors):
        patch.set_facecolor(color)
        patch.set_alpha(0.35)
    ax.set_xticks(range(1, len(labels) + 1), labels)
    ax.set_ylabel("Formatting-only commits\nper 100 commits")
    save(fig, "formatting_only")


def save(fig, name):
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(FIGURES, f"{name}.{ext}"), dpi=200)
    plt.close(fig)


def tex(text):
    return text.replace("_", r"\_").replace("&", r"\&").replace("%", r"\%")


def latex_pairs(adopters, pairs, table):
    """Longtable of all pairs for the paper's appendix."""
    by = {(r["pair"], r["group"]): r for r in table}
    head = r"\toprule Adopter & Adoption & A2 & Pre & Control & Pre \\ \midrule"
    lines = [r"\begin{longtable}{@{}lllrlr@{}}",
             r"\caption{The 54 adopter--control pairs, in order of adoption date. A2: reformat commit confirmed. "
             r"Pre: \texttt{.py} commits in the 24 months before (pseudo-)adoption.}\label{tab:pairs}\\",
             head + r" \endfirsthead", head + r" \endhead", r"\bottomrule \endlastfoot"]
    for p in sorted(pairs, key=lambda p: p["adoption_date"]):
        confirmed = "yes" if adopters[p["adopter"]]["a2_confirmed"] == "True" else "no"
        lines.append(rf"\texttt{{{tex(p['adopter'])}}} & {p['adoption_date'][:10]} & {confirmed} & "
                     rf"{by[(p['adopter'], 'adopter')]['pre_commits']} & \texttt{{{tex(p['control'])}}} & "
                     rf"{by[(p['adopter'], 'control')]['pre_commits']} \\")
    lines.append(r"\end{longtable}")
    return lines


def latex_tests(results, caption, label):
    num = lambda x: f"${x:.3g}$" if abs(x) < 10 else f"{x:.0f}"
    pval = lambda x: "$<$0.001" if x < 0.001 else f"{x:.3f}"
    lines = [r"\begin{table*}[t]", rf"\caption{{{caption}}}", rf"\label{{{label}}}", r"\footnotesize",
             r"\begin{tabular}{@{}lp{4.6cm}rrrrrr@{}}", r"\toprule",
             r"& Comparison & Before & After & $\Delta$ [95\% CI] & $p$ & $p_H$ & $r_{rb}$ \\", r"\midrule"]
    for hyp, name, r in results:
        lines.append(rf"{hyp} & {name} & {num(r['median_x'])} & {num(r['median_y'])} & "
                     rf"{num(r['median_diff'])} [{num(r['ci'][0])}, {num(r['ci'][1])}] & {pval(r['p'])} & {pval(r['p_holm'])} & "
                     rf"${r['effect']:.2f}$ \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
    return lines


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    adopters, pairs = load_sample()
    table = repo_table(adopters, pairs)
    early = repo_table(adopters, pairs, EARLY)
    series = monthly(table)
    os.makedirs(OUT, exist_ok=True)
    write_csv(os.path.join(OUT, "repo_measures.csv"), [{k: v for k, v in r.items() if k != "rows"} for r in table])
    write_csv(os.path.join(OUT, "monthly.csv"), series)
    figures(table, series)

    confirmed = sum(adopters[p["adopter"]]["a2_confirmed"] == "True" for p in pairs)
    near, total, days = style_release_share(table)
    lines = [
        "# Analysis results", "",
        "Generated by `python -m churn.analyze`. Do not edit by hand.", "",
        f"Pairs analysed: {len(pairs)} ({confirmed} with a confirmed reformat commit). "
        "Windows: 24 months before and after the (pseudo-)adoption date; the confirmed reformat commit is excluded. "
        "Formatting share = formatting lines / (formatting + comment + logic lines); unparsed lines and whole new or deleted files are left out.", "",
        "## Sample", "", *sample_description(adopters, pairs), "",
        "## Descriptive statistics", "", *describe(table), "",
        "## Pre-registered tests (all pairs)", "",
        "Wilcoxon signed-rank tests; effect size is the matched-pairs rank-biserial correlation (negative: lower after, or adopters decreased more than controls). "
        "Holm correction over the nine tests in this table.", "", *test_table(tests(table)), "",
        "## Sensitivity: confirmed reformat commit only", "", *test_table(tests(table, confirmed_only=True)), "",
        "## Sensitivity: first 30 days after adoption left out", "", *test_table(tests(early)), "",
        "## Exploratory: incidental formatting (not pre-registered)", "",
        "Formatting share of changed lines counted only in commits that also change code or comments. Holm over these four tests.", "",
        *test_table(exploratory(table)), "",
        "## Between groups (Mann-Whitney U, as in the study plan)", "",
        "Cliff's delta, positive when adopters have larger values.", "",
        *test_table([("-", label, r) for label, r in between_groups(table)]), "",
        "## What formatting-only commits say", "", *(message_table() if os.path.exists(MESSAGES) else ["Run `python -m churn.messages` first."]), "",
        "## Black style releases (exploratory)", "",
        f"{near:,} of {total:,} post-adoption formatting lines in adopters ({100 * near / total:.1f}%) fall within 90 days after one of "
        f"the Black releases that changed the stable style ({', '.join(STYLE_RELEASES)}). "
        f"For comparison, these periods cover at most {days} days per repository, against a 730-day window.", "",
    ]
    appendix = [
        "% Generated by `python -m churn.analyze`. Do not edit by hand.",
        r"\section{Sample}", r"\label{app:pairs}", "", *latex_pairs(adopters, pairs, table), "",
        r"\section{Sensitivity Analyses}", r"\label{app:sensitivity}", "",
        r"Tables~\ref{tab:sens-confirmed} and~\ref{tab:sens-early} repeat the nine pre-registered tests of Table~\ref{tab:tests} "
        "for the pairs whose adopter has a confirmed reformat commit, and without the first 30 days after adoption.", "",
        *latex_tests(tests(table, confirmed_only=True), r"Pre-registered tests, restricted to the 43 pairs with a confirmed "
                     r"reformat commit. Columns as in Table~\ref{tab:tests}.", "tab:sens-confirmed"), "",
        *latex_tests(tests(early), r"Pre-registered tests without the first 30 days after (pseudo-)adoption. "
                     r"Columns as in Table~\ref{tab:tests}.", "tab:sens-early"), "",
        r"\section{Exploratory Tests}", r"\label{app:exploratory}", "",
        *latex_tests(exploratory(table), r"Exploratory tests of the incidental formatting share (formatting share in commits that "
                     r"are not formatting-only). Holm adjustment over these four tests. Columns as in Table~\ref{tab:tests}.", "tab:exploratory"),
    ]
    with open(os.path.join(OUT, "appendix.tex"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(appendix) + "\n")
    with open(os.path.join(OUT, "analysis.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
