# Study protocol

Fixed before the main analysis. Changes after that point are listed in the change log at the end, with the reason.

## Population and sampling

Candidates come from the GitHub search API:

- primary language Python, not a fork, not archived
- at least 500 stars
- created before 2018-01-01, so every repository existed before Black's first release (2018-03-14)
- pushed on or after 2024-01-01
- size below 500 MB

The search is run in one slice per creation year to stay under the 1,000-result limit of the search API. Candidates are shuffled with seed 2026. The first 400 in that order are profiled; this random subset is the basis of the descriptive Black statistics, and the adopter and control groups are drawn from it. The candidate list and the order are saved in `data/candidates.csv`.

## Data sources and cross-checks

### Project start date

| Id | Source | Notes |
|----|--------|-------|
| S1 | Earliest author date of a root commit | Can be older than GitHub when history was imported from SVN or Mercurial |
| S2 | Earliest committer date of a root commit | Differs from S1 after rebases or imports |
| S3 | GitHub `created_at` | Date the GitHub repository was created, not the project |
| S4 | First release on PyPI | Used only when the PyPI project links to the same GitHub repository |

Start date = S1, unless S1 is before 1995 or more than 30 days after S3, in which case S3 is used. Gaps larger than 365 days between S1 and S3 or S4 are reported, not corrected.

### Black adoption date

| Id | Source | Notes |
|----|--------|-------|
| A1 | First commit adding a Black reference to a configuration or automation file | Config can precede actual use |
| A2 | Reformat commit: in [A1 − 180 days, A1 + 365 days], the commit with the most lines classified as formatting | Confirmed when at least 90% of its changed `.py` lines, and at least 50 lines, classify as formatting |
| A3 | Release date of the Black version pinned at A1 (pre-commit `rev` or requirement pin) | A1 earlier than A3 means the pin or the date is wrong |

A1 is read from the first-parent history of the default branch, so for changes made on a branch it is the date the merge reached the default branch. A2 can therefore be a few days earlier than A1.

Adoption date = A2 when it is confirmed, else A1 with a flag. Adoption dates before 2018-03-14 are rejected as errors.

## Groups

**Adopters** must meet all of:

- at least 24 months between the start date and the adoption date
- at least 24 months between adoption and the earlier of: Black removal, a switch to `ruff format`, or the data cut-off
- at least 30 non-merge commits that change `.py` files in each 24-month window
- Black not present in the first commit

**Controls** must meet all of:

- no Black reference before the end of the matched window
- no other enforced formatter (YAPF, autopep8, `ruff format`) in the matched window
- the same activity threshold as adopters

Each control gets a pseudo-adoption date from the adopter it is matched with. Matching is greedy nearest-neighbour without replacement, adopters taken in order of adoption date. The distance is

|ln(1 + pre-window `.py` commits of control) − ln(1 + same for adopter)| + 0.5 · |ln(age of control) − ln(age of adopter)|

with age measured in days from the start date to the (pseudo-)adoption date. Adopters with no eligible control are kept for within-repository analysis and left out of between-group comparisons.

**Exclusions after review:** repositories that are not software projects (courses, tutorials, books, collections of examples) are removed from both groups. Each exclusion is listed with its reason in `data/exclusions.csv`. When an adopter is excluded, its matched control is dropped with it and the remaining pairs are not re-matched.

## Measures

- Formatting, comment, and logic lines per diff hunk, as classified by `churn/classify.py`
- Formatting-only commits: every Python hunk classified as formatting
- Windows: 24 months before and after adoption; the reformat commit is excluded

## Descriptive Black profile (all candidates)

For every profiled candidate, whether or not it is in the final sample:

- whether Black was ever referenced, and when it first and last appears
- how Black is wired in: pre-commit, CI workflow, tox or nox, Makefile, `pyproject.toml`, requirements
- settings at adoption and at HEAD: `line-length`, `skip-string-normalization`, `target-version`
- the pinned Black version and its release date
- whether Black was later replaced by `ruff format`, and when
- other formatters and import sorters present: YAPF, autopep8, isort, `reorder-python-imports`

## Change log

| Date | Change | Reason |
|------|--------|--------|
| 2026-11-21 | A2 is the commit with most formatting lines, not most `.py` files | The file-count rule picked feature commits (for example a commit adding a lecture to a course repository) |
| 2026-11-21 | Non-software repositories excluded after review | Profiling showed course and tutorial repositories among adopters |
