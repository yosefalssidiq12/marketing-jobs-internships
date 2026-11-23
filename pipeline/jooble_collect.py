#!/usr/bin/env python3
"""jooble_collect.py - internal Jooble Egypt discovery collector (prototype).

INTERNAL DISCOVERY ONLY. Writes an internal candidate store
(pipeline/jooble-candidates.json) inside the runner. refresh.yml never commits
this file and it is never merged into the publishable inventory. A candidate
becomes publishable only after its ORIGINAL employer/ATS page is individually
resolved and live-verified in a bounded verification batch (separate stage).
jdp/away resolution is Cloudflare-terminal for server-side requests; there is
no automated resolution path and none is attempted here.

Budget: one eg.jooble.org key, 100 requests/day SHARED across the three Career
repos (not per repo). This script runs only its own vertical's tuned queries,
one page of 20 results per query: ba-pm 6 req/day, ai 5, marketing 6
(17 req/day total baseline). Second pages are reserved headroom, not used here.

Key handling: reads JOOBLE_API_KEY from the environment (Actions secret).
The key is never printed, logged, or written to any file.

Honesty policy:
- Jooble "updated" is the Jooble record refresh date, NOT the posting age.
  It is stored as jooble_record_updated and never presented as a posting date;
  source_posted stays null for jooble records.
- Intermediary/aggregator sources (see INTERMEDIARY_SOURCES) are excluded:
  they are not employer originals and cannot satisfy the verified-original
  publication standard.
- Title-strict relevance: a candidate counts as title-relevant only when its
  TITLE matches the vertical's strict pattern list. Keyword-loose Jooble
  matching means most returned rows are not title-relevant; real counts only,
  no padding.
"""
import json, os, re, sys, time, datetime, urllib.request

OUT = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(OUT, "jooble-candidates.json")
NOW = datetime.datetime.now(datetime.timezone.utc)

QUERIES = {
    "bapm": ["business analyst", "product manager", "project manager",
             "product owner", "scrum master", "management trainee"],
    "ai": ["machine learning engineer", "AI engineer", "data scientist",
           "MLOps", "artificial intelligence"],
    "marketing": ["digital marketing specialist", "performance marketing",
                  "SEO specialist", "content marketing", "social media specialist",
                  "marketing manager"],
}

# Title-strict patterns (title field only, case-insensitive, word-boundary).
# Prototype note: English patterns only; Arabic-titled listings are a known gap
# to measure, not to guess at.
TITLE_PATTERNS = {
    "bapm": [
        r"\bbusiness analyst\b", r"\bproduct manager\b", r"\bproject manager\b",
        r"\bproduct owner\b", r"\bscrum master\b", r"\bprogram manager\b",
        r"\bmanagement trainee\b", r"\bbusiness intelligence analyst\b",
    ],
    "ai": [
        r"\bmachine learning\b", r"\bml engineer\b", r"\bai engineer\b",
        r"\bdata scientist\b", r"\bmlops\b", r"\bartificial intelligence\b",
        r"\bdeep learning\b", r"\bnlp engineer\b", r"\bcomputer vision\b",
    ],
    "marketing": [
        r"\bdigital marketing\b", r"\bperformance marketing\b", r"\bseo\b",
        r"\bcontent marketing\b", r"\bsocial media\b", r"\bmarketing manager\b",
        r"\bmarketing specialist\b", r"\bgrowth marketing\b", r"\bmedia buyer\b",
    ],
}

# Intermediary/aggregator source domains: not employer originals, excluded.
# Maintainable list; additions need observed evidence of intermediary behavior.
INTERMEDIARY_SOURCES = {
    "jobsbylevel.com", "joinrs.com", "talent.flowmingo.ai", "flowmingo.ai",
    "linkedin.com",  # unresolvable to employer original under standing policy
}

MAX_PAGE = 1
RESULTS_PER_PAGE = 20


def post(url, payload, timeout=30):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "career-pipeline-internal-discovery/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    site = None
    if "--site" in sys.argv:
        site = sys.argv[sys.argv.index("--site") + 1]
    if site not in QUERIES:
        print("usage: jooble_collect.py --site bapm|ai|marketing", file=sys.stderr)
        return 2

    key = (os.environ.get("JOOBLE_API_KEY") or "").strip()
    if not key:
        # Not a pipeline failure: Jooble discovery is additive. Record and exit 0
        # so the established refresh is never blocked by the prototype.
        print("jooble_collect: JOOBLE_API_KEY not set; skipping discovery run")
        return 0
    api = "https://eg.jooble.org/api/" + key

    pats = [re.compile(p, re.I) for p in TITLE_PATTERNS[site]]
    errors, per_query = [], []
    candidates = {}

    for q in QUERIES[site]:
        for page in range(1, MAX_PAGE + 1):
            try:
                d = post(api, {"keywords": q, "location": "Egypt",
                               "ResultOnPage": str(RESULTS_PER_PAGE),
                               "page": str(page)})
            except Exception as e:
                errors.append({"query": q, "page": page,
                               "error": type(e).__name__ + ": " + str(e)[:160]})
                print("jooble", q, "ERR", type(e).__name__, file=sys.stderr)
                break
            jobs = d.get("jobs", [])
            n_new = 0
            for j in jobs:
                jid = j.get("id")
                if jid is None or jid in candidates:
                    continue
                title = (j.get("title") or "").strip()
                source = (j.get("source") or "").strip().lower()
                candidates[jid] = {
                    "jooble_id": str(jid),
                    "title": title,
                    "company": (j.get("company") or "").strip(),
                    "location": (j.get("location") or "").strip(),
                    "source_domain": source,
                    "jdp_link": j.get("link"),
                    "type_raw": (j.get("type") or "").strip(),
                    "salary_raw": (j.get("salary") or "").strip(),
                    "snippet": re.sub(r"<[^>]+>", " ", j.get("snippet") or "")[:500],
                    "jooble_record_updated": j.get("updated"),
                    "source_posted": None,  # Jooble gives no posting date; never substitute
                    "fetched_at": NOW.isoformat(),
                    "query": q,
                    "vertical": site,
                    "title_relevant": any(p.search(title) for p in pats),
                    "intermediary_source": source in INTERMEDIARY_SOURCES,
                    # Publication gate: always false here. Promotion requires the
                    # separate bounded individually-verified-original stage.
                    "publishable": False,
                }
                n_new += 1
            per_query.append({"query": q, "page": page,
                              "totalCount_reported": d.get("totalCount"),
                              "returned": len(jobs), "new_unique": n_new})
            time.sleep(1)

    store = {
        "run_at": NOW.isoformat(),
        "vertical": site,
        "budget_note": "one eg key, 100 req/day shared across 3 repos",
        "requests_made": sum(1 for p in per_query),
        "per_query": per_query,
        "errors": errors,
        "unique_candidates": len(candidates),
        "title_relevant": sum(1 for c in candidates.values() if c["title_relevant"]),
        "intermediary_excluded": sum(1 for c in candidates.values() if c["intermediary_source"]),
        "candidates": sorted(candidates.values(),
                             key=lambda c: (not c["title_relevant"], c["title"].lower())),
    }
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)

    # Aggregate counts only in logs: the candidate rows stay in the runner-local
    # store and are never committed or uploaded by this prototype.
    print(f"jooble_collect[{site}]: requests={store['requests_made']} "
          f"unique={store['unique_candidates']} "
          f"title_relevant={store['title_relevant']} "
          f"intermediary_excluded={store['intermediary_excluded']} "
          f"errors={len(errors)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
