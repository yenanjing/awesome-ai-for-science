#!/usr/bin/env python3
"""
update_stars.py — Refresh star counts for all repos in data/repos.json
and regenerate README.md.

Usage:
    python scripts/update_stars.py

Requires:
    pip install requests

Environment:
    GITHUB_TOKEN   GitHub personal access token (optional but strongly
                   recommended — raises rate limit from 60 to 5 000 req/hr)
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import requests

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).resolve().parent.parent
DATA_FILE   = REPO_ROOT / "data" / "repos.json"
README_FILE = REPO_ROOT / "README.md"

# ── GitHub API ───────────────────────────────────────────────────────────────
GITHUB_API  = "https://api.github.com/repos/{}"
HEADERS: dict[str, str] = {"Accept": "application/vnd.github+json"}
if token := os.getenv("GITHUB_TOKEN"):
    HEADERS["Authorization"] = f"Bearer {token}"

SLEEP_BETWEEN = 0.5   # seconds between requests (well within rate limits)
SEARCH_SLEEP  = 7     # seconds between GitHub Search API calls (≤30 req/min with token)
MIN_STARS     = 10    # minimum stars required to keep / add a repo

# ── Search queries: category → list[query_string] ────────────────────────────
# All queries include stars:>10 so the API pre-filters results.
SEARCH_QUERIES: dict[str, list[str]] = {
    "\U0001f9d1\u200d\U0001f52c AI Scientist Frameworks": [
        "topic:ai-scientist stars:>10",
        '"ai scientist" autonomous experiment paper stars:>10',
    ],
    "\U0001f501 Autoresearch & Self-Improving Loops": [
        "topic:autoresearch stars:>10",
        '"research loop" autonomous llm stars:>10',
    ],
    "\U0001f310 Deep Research Agents": [
        "topic:deep-research stars:>10",
        '"deep research" agent llm stars:>10',
    ],
    "\U0001f4a1 Hypothesis Generation & Idea Mining": [
        '"hypothesis generation" ai research stars:>10',
        '"idea generation" research llm stars:>10',
    ],
    "\U0001f4dd Paper Writing & Academic Automation": [
        '"paper writing" autonomous ai stars:>10',
        '"academic writing" llm automation stars:>10',
    ],
    "\U0001f4da Literature Review & Paper Search": [
        '"literature review" autonomous ai stars:>10',
        '"paper search" semantic llm stars:>10',
    ],
    "\U0001f9ec AI for Drug Discovery & Biology": [
        "topic:drug-discovery ai agent stars:>10",
        '"drug discovery" llm agent stars:>10',
    ],
    "\u2697\ufe0f AI for Materials & Physical Sciences": [
        '"materials discovery" ai agent stars:>10',
        '"materials science" llm autonomous stars:>10',
    ],
    "\U0001f52c Research Tools & Infrastructure": [
        '"research infrastructure" ai agent stars:>10',
        '"experiment automation" ai framework stars:>10',
    ],
    "\U0001f4d6 Curated Lists & Resources": [
        '"awesome ai for science" stars:>10',
        '"awesome ai scientist" stars:>10',
    ],
}

# ── Category config ──────────────────────────────────────────────────────────
CATEGORY_ORDER = [
    "\U0001f9d1\u200d\U0001f52c AI Scientist Frameworks",
    "\U0001f501 Autoresearch & Self-Improving Loops",
    "\U0001f310 Deep Research Agents",
    "\U0001f4a1 Hypothesis Generation & Idea Mining",
    "\U0001f4dd Paper Writing & Academic Automation",
    "\U0001f4da Literature Review & Paper Search",
    "\U0001f9ec AI for Drug Discovery & Biology",
    "\u2697\ufe0f AI for Materials & Physical Sciences",
    "\U0001f52c Research Tools & Infrastructure",
    "\U0001f4d6 Curated Lists & Resources",
]

CAT_DESC = {
    "\U0001f9d1\u200d\U0001f52c AI Scientist Frameworks":
        'End-to-end autonomous systems that can ideate, experiment, and write papers — the "AI Scientist" paradigm.',
    "\U0001f501 Autoresearch & Self-Improving Loops":
        "Self-improving research pipelines, autoresearch frameworks, and autonomous coding/experiment loops.",
    "\U0001f310 Deep Research Agents":
        "Agents that perform deep, multi-step research by synthesizing information from the web, documents, and databases.",
    "\U0001f4a1 Hypothesis Generation & Idea Mining":
        "Tools for surfacing novel research directions, generating hypotheses, and mining ideas from literature.",
    "\U0001f4dd Paper Writing & Academic Automation":
        "Automated paper writing, academic formatting, LaTeX generation, and citation management.",
    "\U0001f4da Literature Review & Paper Search":
        "Tools for automated literature review, semantic search over papers, and research discovery.",
    "\U0001f9ec AI for Drug Discovery & Biology":
        "Domain-specific AI accelerating drug discovery, protein folding, genomics, and biomedical research.",
    "\u2697\ufe0f AI for Materials & Physical Sciences":
        "AI applications in materials science, chemistry, physics, and related physical sciences.",
    "\U0001f52c Research Tools & Infrastructure":
        "Infrastructure, frameworks, and utility tools that power AI research automation.",
    "\U0001f4d6 Curated Lists & Resources":
        "Awesome lists, paper collections, and curated resources for AI-driven research.",
}

TOC_ANCHORS = {
    "\U0001f9d1\u200d\U0001f52c AI Scientist Frameworks":    "ai-scientist-frameworks",
    "\U0001f501 Autoresearch & Self-Improving Loops":        "autoresearch-self-improving-loops",
    "\U0001f310 Deep Research Agents":                       "deep-research-agents",
    "\U0001f4a1 Hypothesis Generation & Idea Mining":        "hypothesis-generation-idea-mining",
    "\U0001f4dd Paper Writing & Academic Automation":        "paper-writing-academic-automation",
    "\U0001f4da Literature Review & Paper Search":           "literature-review-paper-search",
    "\U0001f9ec AI for Drug Discovery & Biology":            "ai-for-drug-discovery-biology",
    "\u2697\ufe0f AI for Materials & Physical Sciences":     "ai-for-materials-physical-sciences",
    "\U0001f52c Research Tools & Infrastructure":            "research-tools-infrastructure",
    "\U0001f4d6 Curated Lists & Resources":                  "curated-lists-resources",
}


# ── GitHub Search helpers ─────────────────────────────────────────────────────
def search_github(query: str) -> list[dict]:
    """Search GitHub repositories matching *query*. Returns raw API items."""
    url = "https://api.github.com/search/repositories"
    params = {"q": query, "sort": "updated", "order": "desc", "per_page": 100}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if resp.status_code == 200:
            return resp.json().get("items", [])
        if resp.status_code == 403:
            reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait  = max(reset - int(time.time()), 0) + 5
            print(f"  [403] Search rate-limited. Waiting {wait}s …")
            time.sleep(wait)
            resp2 = requests.get(url, headers=HEADERS, params=params, timeout=20)
            if resp2.status_code == 200:
                return resp2.json().get("items", [])
        print(f"  [Search {resp.status_code}] {query!r}")
    except requests.RequestException as exc:
        print(f"  [Search ERR] {query!r}: {exc}")
    return []


def normalize_repo(gh_item: dict, category: str) -> dict:
    """Convert a GitHub Search API result item to the internal schema."""
    return {
        "name":        gh_item["full_name"],
        "url":         gh_item["html_url"],
        "description": gh_item.get("description") or "",
        "stars":       gh_item["stargazers_count"],
        "forks":       gh_item.get("forks_count", 0),
        "language":    gh_item.get("language") or "",
        "topics":      ",".join(gh_item.get("topics") or []),
        "updated":     gh_item.get("updated_at", ""),
        "category":    category,
    }


def discover_repos(existing_urls: set[str]) -> list[dict]:
    """
    Run all SEARCH_QUERIES; return newly-discovered repos not already in
    *existing_urls* and with stars > MIN_STARS.
    *existing_urls* is mutated in-place to deduplicate across queries.
    """
    new_repos: list[dict] = []
    for category, queries in SEARCH_QUERIES.items():
        for query in queries:
            print(f"  Searching: {query!r}")
            items = search_github(query)
            for item in items:
                html_url = item.get("html_url", "")
                if html_url in existing_urls:
                    continue
                if item.get("stargazers_count", 0) <= MIN_STARS:
                    continue
                new_repos.append(normalize_repo(item, category))
                existing_urls.add(html_url)
            time.sleep(SEARCH_SLEEP)
    print(f"Discovered {len(new_repos)} new repos")
    return new_repos


# ── Star fetching ─────────────────────────────────────────────────────────────
def fetch_stars(full_name: str) -> int | None:
    """Return current star count for <owner>/<repo>, or None on error."""
    url = GITHUB_API.format(full_name)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("stargazers_count")
        if resp.status_code == 404:
            print(f"  [404] {full_name} — repo not found (archived/deleted?)")
        elif resp.status_code == 403:
            reset = int(resp.headers.get("X-RateLimit-Reset", 0))
            wait  = max(reset - int(time.time()), 1)
            print(f"  [403] Rate limited. Waiting {wait}s …")
            time.sleep(wait + 1)
            return fetch_stars(full_name)   # retry once
        else:
            print(f"  [{resp.status_code}] {full_name}")
    except requests.RequestException as exc:
        print(f"  [ERR] {full_name}: {exc}")
    return None


# ── README generation ─────────────────────────────────────────────────────────
def fmt_row(r: dict) -> str:
    name  = r["name"]
    url   = r["url"]
    stars = r["stars"]
    lang  = f"`{r['language']}`" if r.get("language") else ""
    desc  = (r.get("description") or "").replace("|", "\\|")
    if len(desc) > 120:
        desc = desc[:117] + "..."
    return f"| [**{name}**]({url}) | ⭐ {stars:,} | {lang} | {desc} |"


def generate_readme(repos_by_cat: dict[str, list[dict]], today: str) -> str:
    all_repos = [r for cat in CATEGORY_ORDER for r in repos_by_cat.get(cat, [])]
    total     = len(all_repos)
    num_cats  = sum(1 for cat in CATEGORY_ORDER if repos_by_cat.get(cat))

    lang_counter = Counter(r["language"] for r in all_repos if r.get("language"))
    top_langs    = ", ".join(f"{l}({c})" for l, c in lang_counter.most_common(8))

    seen: set[str] = set()
    top20: list[dict] = []
    for r in sorted(all_repos, key=lambda x: -x["stars"]):
        if r["url"] not in seen:
            seen.add(r["url"])
            top20.append(r)
        if len(top20) == 20:
            break

    lines: list[str] = []

    # Header
    lines += [
        '<div align="center">',
        "  <h1>🔬 Awesome AI for Scientific Research</h1>",
        "  <p>A curated list of awesome projects using AI to automate scientific research — autonomous research agents, AI scientists, hypothesis generation, paper writing, deep research, and more.</p>",
        "",
        "  [![Awesome](https://awesome.re/badge.svg)](https://awesome.re)",
        "  ![GitHub stars](https://img.shields.io/github/stars/yenanjing/awesome-ai-for-science?style=flat-square)",
        f"  ![Last Updated](https://img.shields.io/badge/last%20updated-{today}-blue?style=flat-square)",
        "",
        f"  <p>Collected <strong>{total}</strong> repositories across <strong>{num_cats}</strong> categories covering the full spectrum of AI-driven research automation.</p>",
        "</div>",
        "",
        "---",
        "",
        "## 📖 Table of Contents",
        "",
        "- [About](#-about)",
        "",
    ]
    for cat in CATEGORY_ORDER:
        if repos_by_cat.get(cat):
            anchor = TOC_ANCHORS.get(cat, "")
            lines.append(f"- [{cat}](#{anchor})")
    lines += [
        "- [📊 Stats](#-stats)",
        "- [⭐ Star History](#-star-history)",
        "- [🤝 Contributing](#-contributing)",
        "",
        "---",
        "",
        "## 🌟 About",
        "",
        "This list focuses specifically on **AI systems that automate the scientific research process** — from generating hypotheses and searching literature to running experiments, analyzing results, and writing papers. It covers:",
        "",
        "- 🧑‍🔬 **End-to-end AI scientists** that autonomously conduct research (Sakana AI Scientist, etc.)",
        "- 🔁 **Self-improving research loops** inspired by Karpathy's autoresearch paradigm",
        "- 🌐 **Deep research agents** that synthesize knowledge from the web and documents",
        "- 💡 **Hypothesis generation** systems that surface novel research directions",
        "- 📝 **Automated paper writing** and academic productivity tools",
        "- 🧬 **Domain-specific AI** accelerating drug discovery, materials science, and more",
        "",
        "> **Note**: This list focuses on AI *automating* research — not general AI tools, ML frameworks, or unrelated agents.",
        f"> Last updated: {today}",
        "",
        "---",
        "",
    ]

    # Category sections
    for cat in CATEGORY_ORDER:
        repos = repos_by_cat.get(cat, [])
        if not repos:
            continue
        desc = CAT_DESC.get(cat, "")
        lines += [
            f"## {cat}",
            "",
            f"> {desc}",
            "",
            "| Repository | Stars | Language | Description |",
            "|-----------|-------|----------|-------------|",
        ]
        for r in repos:
            lines.append(fmt_row(r))
        lines += ["", "---", ""]

    # Stats
    lines += [
        "## 📊 Stats",
        "",
        f"- **Total repositories**: {total}",
        f"- **Categories**: {num_cats}",
        f"- **Top languages**: {top_langs}",
        f"- **Last updated**: {today}",
        "",
        "### 🏆 Top 20 by Stars",
        "",
        "| Rank | Repository | Stars | Description |",
        "|------|-----------|-------|-------------|",
    ]
    for i, r in enumerate(top20, 1):
        desc = (r.get("description") or "").replace("|", "\\|")
        if len(desc) > 80:
            desc = desc[:77] + "..."
        lines.append(f"| {i} | [{r['name']}]({r['url']}) | ⭐ {r['stars']:,} | {desc} |")

    lines += [
        "",
        "---",
        "",
        "## ⭐ Star History",
        "",
        "[![Star History Chart](https://api.star-history.com/svg?repos=yenanjing/awesome-ai-for-science&type=Date)](https://star-history.com/#yenanjing/awesome-ai-for-science&Date)",
        "",
        "---",
        "",
        "## 🤝 Contributing",
        "",
        "Contributions are welcome! Please read the [contribution guidelines](CONTRIBUTING.md) first.",
        "",
        "To add a project:",
        "1. Fork this repository",
        "2. Add your project to the relevant section",
        "3. The project must be focused on **AI automating scientific research** (not general AI tools)",
        "4. Submit a Pull Request",
        "",
        "---",
        "",
        "## 📄 License",
        "",
        "[![CC0](https://licensebuttons.net/p/zero/1.0/88x31.png)](https://creativecommons.org/publicdomain/zero/1.0/)",
        "",
        "This list is under the [CC0 1.0](LICENSE) license.",
        "",
        "---",
        "",
        '<div align="center">',
        '  <sub>Generated with ❤️ using <a href="https://claude.ai/claude-code">Claude Code</a></sub>',
        "</div>",
    ]
    return "\n".join(lines) + "\n"


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] Loading {DATA_FILE}")
    with DATA_FILE.open() as f:
        repos: list[dict] = json.load(f)

    total = len(repos)
    print(f"  {total} repos to update")

    updated = skipped = errors = 0
    for i, repo in enumerate(repos, 1):
        full_name = repo["name"]   # format: "owner/repo"
        new_stars = fetch_stars(full_name)
        if new_stars is None:
            errors += 1
        elif new_stars != repo["stars"]:
            print(f"  [{i}/{total}] {full_name}: {repo['stars']} → {new_stars}")
            repo["stars"] = new_stars
            updated += 1
        else:
            skipped += 1
        time.sleep(SLEEP_BETWEEN)

    print(f"\nDone: {updated} updated, {skipped} unchanged, {errors} errors")

    # Drop repos with stars <= MIN_STARS
    before = len(repos)
    repos = [r for r in repos if r.get("stars", 0) > MIN_STARS]
    dropped = before - len(repos)
    print(f"Dropped {dropped} repos with ≤{MIN_STARS} stars  ({len(repos)} remain)")

    # Discover new repos via GitHub Search
    print("\n--- Discovering new repos ---")
    existing_urls: set[str] = {r["url"] for r in repos}
    new_repos = discover_repos(existing_urls)
    repos.extend(new_repos)
    print(f"Total repos after discovery: {len(repos)}")

    # Persist updated data
    with DATA_FILE.open("w") as f:
        json.dump(repos, f, indent=2, ensure_ascii=False)
    print(f"Saved {DATA_FILE}")

    # Re-group by category
    repos_by_cat: dict[str, list[dict]] = {cat: [] for cat in CATEGORY_ORDER}
    for repo in repos:
        cat = repo.get("category", "")
        if cat in repos_by_cat and repo["stars"] > MIN_STARS:
            repos_by_cat[cat].append(repo)

    # Sort each category by stars desc
    for cat in repos_by_cat:
        repos_by_cat[cat].sort(key=lambda x: -x["stars"])

    today = date.today().isoformat()
    readme = generate_readme(repos_by_cat, today)
    README_FILE.write_text(readme, encoding="utf-8")
    print(f"Saved {README_FILE}")


if __name__ == "__main__":
    main()
