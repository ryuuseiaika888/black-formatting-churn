# Black usage in the profiled candidates

Profiled: 400; errors: 0; analysed: 400.

## Adoption

| Measure | Value |
|---|---|
| Black referenced at some point | 125 (31%) |
| Black referenced at HEAD | 68 (17%) |
| Black users that later added `ruff format` | 63 (50%) |
| Black removed, then reintroduced | 3 (2%) |
| `ruff format` without Black ever | 24 (6%) |
| isort referenced, among Black users | 91 (73%) |
| reorder_imports referenced, among Black users | 8 (6%) |
| yapf referenced, among Black users | 9 (7%) |
| autopep8 referenced, among Black users | 7 (6%) |

### First Black reference (A1) by year

| Year | Repositories |
|---|---|
| 2018 | 11 |
| 2019 | 19 |
| 2020 | 26 |
| 2021 | 25 |
| 2022 | 18 |
| 2023 | 13 |
| 2024 | 7 |
| 2025 | 6 |

### How Black is wired in at A1

| Mechanism | At A1 | Ever |
|---|---|---|
| project_config | 62 (50%) | 86 (69%) |
| pre_commit | 41 (33%) | 65 (52%) |
| ci | 39 (31%) | 47 (38%) |
| task_runner | 31 (25%) | 45 (36%) |
| requirements | 29 (23%) | 41 (33%) |

### Settings in `pyproject.toml` at A1

| Setting | Repositories |
|---|---|
| `line-length` set | 34 (27%); values: 79: 12, 120: 8, 100: 4, 88: 3, 80: 2 |
| `skip-string-normalization = true` | 9 (7%) |

## Cross-checks

### Project start date

| Check | Value |
|---|---|
| Start date taken from S1 (first commit) | 387 (97%) |
| S1 more than 365 days before GitHub creation (S3) | 59 (15%) |
| PyPI project linked to the repository (S4 available) | 201 (50%) |
| S4 more than 365 days away from S1 | 78 (39%) |
| Days from S1 to S3 | median 0 (IQR -1 to 12), n=400 |

### Black adoption date

| Check | Value |
|---|---|
| Days from A1 (config) to A2 (largest `.py` commit near A1) | median 0 (IQR -1 to 36), n=125 |
| A2 within 30 days of A1 | 83 (66%) |
| Black version pinned at A1 | 42 (34%) |
| A1 earlier than the pinned version's release (error) | 0 (0%) |
| Days from pinned release to A1 | median 58 (IQR 31 to 142), n=42 |
| A1 before Black's first release (error) | 0 |
| Months from project start to adoption | median 90 (IQR 65 to 117), n=125 |

## Eligibility (docs/protocol.md)

| Group | Repositories |
|---|---|
| Adopters meeting all rules | 56 |
| Control candidates before matching | 177 |
