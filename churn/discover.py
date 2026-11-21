"""Search GitHub for candidate repositories, one query per creation year.

Usage: python -m churn.discover OUT_CSV

Uses GITHUB_TOKEN when set; without it the search API allows 10 requests a minute.
"""
import csv
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request

QUERY = "language:Python stars:>=500 fork:false archived:false pushed:>=2024-01-01 size:<500000 created:{start}..{end}"
YEARS = range(2008, 2018)
SEED = 2026
FIELDS = ["order", "full_name", "created_at", "pushed_at", "stars", "size_kb", "default_branch", "clone_url"]


def _get(url):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "black-formatting-churn"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    while True:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (403, 429):
                raise
            reset = int(error.headers.get("X-RateLimit-Reset", time.time() + 60))
            time.sleep(max(reset - time.time(), 1) + 2)


def search_year(year):
    query = QUERY.format(start=f"{year}-01-01", end=f"{year}-12-31")
    for page in range(1, 11):
        params = urllib.parse.urlencode({"q": query, "sort": "stars", "order": "desc", "per_page": 100, "page": page})
        result = _get("https://api.github.com/search/repositories?" + params)
        if page == 1:
            print(f"{year}: {result['total_count']} matches", file=sys.stderr)
        yield from result["items"]
        if len(result["items"]) < 100:
            return


def main(out_csv):
    repos = {}
    for year in YEARS:
        for item in search_year(year):
            repos[item["full_name"]] = {
                "full_name": item["full_name"], "created_at": item["created_at"], "pushed_at": item["pushed_at"],
                "stars": item["stargazers_count"], "size_kb": item["size"],
                "default_branch": item["default_branch"], "clone_url": item["clone_url"],
            }
    names = sorted(repos)
    random.Random(SEED).shuffle(names)
    with open(out_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, FIELDS)
        writer.writeheader()
        for order, name in enumerate(names):
            writer.writerow({"order": order, **repos[name]})
    print(f"{len(names)} candidates written to {out_csv}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
