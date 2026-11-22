#!/usr/bin/env python3
"""Career pipeline collector v2 — established API sources only (Jobicy, Remotive, Arbeitnow).

Pipeline order: collect.py -> classify.py -> verify.py (verify.py is the ONLY writer of last_verified).
- fetched_at: when the source feed/API returned the record. Never treated as verification.
- last_verified: always null here; set only by verify.py with a method string.
- first_seen/last_seen: persisted across runs in pipeline-state.json (runner-local artifact, not a DB).
- Dedup: canonical URL, then normalized title+company with a location-conflict guard; merged records
  keep also_on / url_variants / location_variants. No silent drops.
- Freshness gate: EXPERIMENTAL parameter, archive-not-delete.
Local build/test only: no publish, no schedule, no DB."""
import json, re, time, hashlib, urllib.request, urllib.parse, datetime, sys, os

OUT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(OUT, "pipeline-state.json")
STAGED_STATE = STATE + ".next"  # written pre-gate; refresh.py promotes only after the publication gate passes
NOW = datetime.datetime.now(datetime.timezone.utc)
FRESH_DAYS = 30  # EXPERIMENTAL, not user-approved policy

def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "career-pipeline-local-test/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def canon(url):
    if not url: return None
    url = url.strip()
    if not url.lower().startswith("http"): return None
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower()
    path = re.sub(r"/+$", "", p.path)
    qs = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
          if not k.lower().startswith(("utm_", "ref", "fbclid", "gclid", "tracking"))]
    return urllib.parse.urlunsplit(("https", host, path, urllib.parse.urlencode(qs), ""))

def jid(url): return hashlib.sha1(url.encode()).hexdigest()[:16]

def parse_date(s):
    if not s: return None
    if isinstance(s, (int, float)):
        return datetime.datetime.fromtimestamp(s, datetime.timezone.utc)
    s = str(s).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None

def clean_company(name):
    """Cosmetic normalization only: whitespace + source-artifact suffix glued by a feed.
    Does NOT strip legal forms (GmbH/Inc/LLC) — subsidiaries stay distinct (QA F3)."""
    n = re.sub(r"\s+", " ", (name or "").strip())
    n = re.sub(r"(?<=[a-zA-Z\u00C0-\u024F]{3})(linkedin|indeed|xing|glassdoor)$", "", n, flags=re.I).strip()
    return n

def norm_txt(s): return re.sub(r"[^a-z0-9\u00C0-\u024F]+", "", (s or "").lower())

GENERIC_LOC = {"remote", "anywhere", "worldwide", "global", "emea", "eu", "europe", ""}
def loc_tokens(loc):
    return {t for t in re.findall(r"[a-z\u00C0-\u024F]{3,}", (loc or "").lower())} - GENERIC_LOC

def loc_conflict(a, b):
    ta, tb = loc_tokens(a), loc_tokens(b)
    return bool(ta and tb and ta.isdisjoint(tb))

jobs = []
def add(source, source_id, title, company, url, posted, desc, loc, jtype):
    c = canon(url)
    if not c or not title or not company: return
    jobs.append({
        "id": jid(c), "title": str(title).strip(), "company": clean_company(company),
        "url": c, "source": source, "source_id": str(source_id) if source_id else None,
        "source_posted": posted.isoformat() if posted else None,
        "fetched_at": NOW.isoformat(), "last_verified": None, "verification": None,
        "verified_this_run": None,
        "location": (loc or "").strip(), "type_raw": (jtype or "").strip(),
        "desc": re.sub(r"<[^>]+>", " ", desc or "")[:3000],
    })

ERRORS = []  # per-source/scope collection errors; refresh.py fails closed on any entry

def fetch_all():
    for ind in ["marketing", "seo", "project-management", "management", "business", "data-science"]:
        try:
            d = get(f"https://jobicy.com/api/jobs?count=200&industry={ind}")
            for j in d.get("jobs", []):
                add("jobicy", j.get("id"), j.get("jobTitle"), j.get("companyName"), j.get("url"),
                    parse_date(j.get("pubDate")), j.get("jobDescription") or "",
                    ", ".join(j.get("jobGeo", []) if isinstance(j.get("jobGeo"), list) else [str(j.get("jobGeo") or "")]),
                    ", ".join(j.get("jobType", []) if isinstance(j.get("jobType"), list) else [str(j.get("jobType") or "")]))
            time.sleep(1)
        except Exception as e:
            print("jobicy", ind, "ERR", e, file=sys.stderr)
            ERRORS.append({"source": "jobicy", "scope": str(ind), "error": type(e).__name__ + ": " + str(e)[:160]})
    for cat in (["marketing", "product", "project-management", "artificial-intelligence"] if os.environ.get("CAREER_ENABLE_REMOTIVE") == "1" else []):  # REMOTIVE EXCLUDED by default (standing policy)
        try:
            d = get(f"https://remotive.com/api/remote-jobs?category={cat}")
            for j in d.get("jobs", []):
                add("remotive", j.get("id"), j.get("title"), j.get("company_name"), j.get("url"),
                    parse_date(j.get("publication_date")), j.get("description") or "",
                    j.get("candidate_required_location") or "", j.get("job_type") or "")
            time.sleep(1)
        except Exception as e:
            print("remotive", cat, "ERR", e, file=sys.stderr)
            ERRORS.append({"source": "remotive", "scope": str(cat), "error": type(e).__name__ + ": " + str(e)[:160]})
    for page in [1, 2, 3]:
        try:
            d = get(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
            for j in d.get("data", []):
                add("arbeitnow", j.get("slug"), j.get("title"), j.get("company_name"), j.get("url"),
                    parse_date(j.get("created_at")), j.get("description") or "",
                    j.get("location") or "", ", ".join(j.get("job_types", []) or []))
            if not d.get("links", {}).get("next"): break
            time.sleep(1)
        except Exception as e:
            print("arbeitnow", page, "ERR", e, file=sys.stderr)
            ERRORS.append({"source": "arbeitnow", "scope": str(page), "error": type(e).__name__ + ": " + str(e)[:160]})

def dedup(jobs):
    by_url = {}
    url_dupes = 0
    for j in jobs:
        if j["url"] in by_url: url_dupes += 1
        else: by_url[j["url"]] = j
    merged, tc_dupes, guarded = [], 0, 0
    by_tc = {}
    for j in by_url.values():
        k = norm_txt(j["title"]) + "|" + norm_txt(j["company"])
        if k in by_tc:
            keep = by_tc[k]
            if loc_conflict(keep.get("location"), j.get("location")):
                # same title+company but genuinely different locations: do not merge (subsidiary/region guard)
                guarded += 1
                merged.append(j)
                continue
            tc_dupes += 1
            keep.setdefault("also_on", [])
            if j["source"] not in keep.get("also_on", []) and j["source"] != keep["source"]:
                keep["also_on"].append(j["source"])
            if j["url"] != keep["url"]:
                keep.setdefault("url_variants", []).append(j["url"])
            if j.get("location") and j["location"] != keep.get("location"):
                keep.setdefault("location_variants", [])
                if j["location"] not in keep["location_variants"]:
                    keep["location_variants"].append(j["location"])
        else:
            by_tc[k] = j
            merged.append(j)
    return merged, url_dupes, tc_dupes, guarded

def apply_freshness(jobs, now, days):
    """EXPERIMENTAL gate: archive-not-delete. Extracted for fixture testing (test_freshness.py)."""
    cutoff = now - datetime.timedelta(days=days)
    active, archived = [], []
    for j in jobs:
        pd = parse_date(j.get("source_posted"))
        if pd and pd < cutoff:
            j["archive_reason"] = f"source_posted older than {days}d gate (EXPERIMENTAL)"
            archived.append(j)
        else:
            active.append(j)
    return active, archived


def merge_state(joblist, state, now_iso):
    """Cross-run identity persistence (QA F6). first_seen survives reruns; last_seen advances;
    last_verified carries only while the canonical URL is unchanged."""
    for j in joblist:
        prev = state.get(j["id"], {})
        j["first_seen"] = prev.get("first_seen") or now_iso
        j["last_seen"] = now_iso
        if prev.get("last_verified") and prev.get("url") == j["url"]:
            j["last_verified"] = prev["last_verified"]
            j["verification"] = prev.get("verification")
        else:
            j["last_verified"] = j.get("last_verified")  # keep whatever verify.py set this run
        state[j["id"]] = {"first_seen": j["first_seen"], "last_seen": j["last_seen"],
                          "url": j["url"], "last_verified": j.get("last_verified"),
                          "verification": j.get("verification")}
    return state

def main():
    fetch_all()
    raw = len(jobs)
    deduped, url_dupes, tc_dupes, guarded = dedup(jobs)
    active, archived = apply_freshness(deduped, NOW, FRESH_DAYS)

    # cross-run state: first_seen / last_seen / last_verified persistence (F6)
    state = {}
    if os.path.exists(STATE):
        try: state = json.load(open(STATE))
        except Exception: state = {}
    state = merge_state(active + archived, state, NOW.isoformat())
    json.dump(state, open(STAGED_STATE, "w"), ensure_ascii=False, indent=1)  # staged, not accepted

    out = {"generated_at": NOW.isoformat(),
           "field_semantics": {
               "fetched_at": "when the source feed/API returned this record",
               "first_seen": "first run that saw this canonical job (persisted in pipeline-state.json)",
               "last_seen": "most recent run that saw this canonical job",
               "last_verified": "null until verify.py ran a listing-level check; never set from feed fetch",
               "fresh_days_gate": "EXPERIMENTAL parameter (30d), not user-approved policy"},
           "counts": {"raw": raw, "url_dupes_removed": url_dupes,
                      "title_company_dupes_merged": tc_dupes, "location_guard_kept_separate": guarded,
                      "active": len(active), "archived": len(archived)},
           "errors": ERRORS,
           "jobs": active, "archived": archived}
    json.dump(out, open(os.path.join(OUT, "collected.json"), "w"), ensure_ascii=False, indent=1)
    print("raw", raw, "| url_dupes", url_dupes, "| tc_merged", tc_dupes, "| loc_guard", guarded,
          "| active", len(active), "| archived", len(archived))

if __name__ == "__main__":
    main()
