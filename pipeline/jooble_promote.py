#!/usr/bin/env python3
"""jooble_promote.py - same-run bounded promotion of internal Jooble candidates.

Reads the runner-local internal store (pipeline/jooble-candidates.json) and, for
eligible candidates (title-relevant AND non-intermediary), tries to resolve and
individually verify the ORIGINAL employer/ATS page through permitted public
routes ONLY (the employer's own board page or the ATS's official public API).
The Jooble jdp/away route is never touched (Cloudflare-terminal; no retries).

Fail-closed gates, ALL required before a candidate is marked promoted:
  1. resolved: original URL found via a permitted resolver for its source_domain
  2. live: original page fetched, HTTP 200
  3. match: company AND title both match the original page (ambiguity = reject)
  4. active: no expiry/closed notice; where the resolver is a live board/API,
     presence in that live listing is the positive active signal
  5. geography: page states Egypt, or a remote scope that includes Egypt;
     unknown/unstated location = reject
  6. role fit: duties evidence in the description (responsibilities marker +
     minimum length); no duties evidence = reject
Anything failing any gate stays internal with its reason recorded. Honest zero
is a valid outcome. Request budget: at most 2 fetches per candidate, 1s pacing,
and an immediate stop on HTTP 403/429 or a challenge marker.

v6 additions (integrated v3 pipeline):
  - --prev-data PATH: previous published release JSON. Rows with source "Jooble"
    are RE-CHECKED FIRST each run (site integrity): their original URL is
    fetched and re-verified against the same gates. Pass -> promotion record
    with first_seen preserved from the card. Fail (expired/404/mismatch/geo
    gone) OR transient error -> NOT re-emitted; recorded in dropped_published
    with the reason. Because the base refresh carries zero-day-stale rows, a
    dropped card leaves the site the same run: removal is evidence-based and
    fail-closed.
  - Fetch budget: at most --max-fetches outbound fetches total (re-checks
    first, then new candidates). Unchecked published rows are dropped with
    reason "recheck_budget" (fail-closed; the v6 publication model requires
    same-day verification).
  - Promotion records carry display evidence (geo/work/schedule/duties
    snippet) extracted from the verified original page, for the fold step.

Output: pipeline/jooble-promotions.json (runner-local, never committed).
"""
import json, os, re, sys, time, datetime, urllib.request, urllib.error, html

OUT = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(OUT, "jooble-candidates.json")
PROMO = os.path.join(OUT, "jooble-promotions.json")
NOW = datetime.datetime.now(datetime.timezone.utc)
TODAY = NOW.date().isoformat()
UA = {"User-Agent": "career-pipeline-verification/0.1"}
STOP = {"hit": None}  # challenge/throttle circuit breaker
FETCHES = {"n": 0, "max": 12}  # outbound fetch budget (re-checks first)
CHALLENGE = re.compile(r"just a moment|cf-chl-|challenge-platform|attention required! \| cloudflare", re.I)

EXPIRY = re.compile(r"no longer accepting|job has expired|position has been filled|"
                    r"this job is no longer available|job not found|404", re.I)
DUTIES = re.compile(r"responsibilit|what you.ll do|your role|duties|مهام|المسؤوليات", re.I)
EGYPT = re.compile(r"\begypt\b|\bcairo\b|\balexandria\b|\bgiza\b|مصر|القاهرة", re.I)
# Generic "remote" is NOT Egypt eligibility. Only an explicitly global scope
# passes, and only when no conflicting country/region restriction is present.
REMOTE_GLOBAL = re.compile(r"remote.{0,40}(worldwide|anywhere|global|work from anywhere)|"
                           r"(worldwide|anywhere|global).{0,40}remote", re.I)
REMOTE_RESTRICT = re.compile(r"remote.{0,40}\b(US|USA|United States|EU|Europe|EMEA-only|UK|Canada)\b.{0,20}(only|based|required)|"
                             r"\b(US|USA|United States|EU|Europe|UK|Canada)\b.{0,10}(only|residents?|based)|"
                             r"(based|located|residing|eligible).{0,20}\bin\b.{0,10}\b(the )?(US|USA|United States|EU|Europe|UK|Canada)\b", re.I)


def fetch(url, timeout=20):
    if STOP["hit"]:
        return None, "stopped_after_" + STOP["hit"]
    if FETCHES["n"] >= FETCHES["max"]:
        return None, "budget_exhausted"
    FETCHES["n"] += 1
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            if CHALLENGE.search(body):
                STOP["hit"] = "challenge"
                return None, "challenge"
            return body, None
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            STOP["hit"] = str(e.code)
        return None, "http_" + str(e.code)
    except Exception as e:
        return None, type(e).__name__


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    return s


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def resolve_breezy(cand):
    """Breezy board: https://<companyslug>.breezy.hr lists only live jobs.
    Positive presence on the live board = active signal. 1 fetch."""
    slug = slugify(cand["company"])
    if not slug:
        return None, "no_slug"
    board, err = fetch(f"https://{slug}.breezy.hr")
    if err:
        return None, "board_" + err
    time.sleep(1)
    # breezy board links: href="/p/<id>-<title-slug>" or full URL variants
    # breezy board anchors carry the job title in the URL slug tail:
    # /p/<id>-<title-slug>; match the slug tail exactly against the title.
    want_slug = re.sub(r"[^a-z0-9]+", "-", cand["title"].lower()).strip("-")
    if not want_slug:
        return None, "no_title_slug"
    for m in re.finditer(r'href="((?:https://' + re.escape(slug) +
                         r'\.breezy\.hr)?/p/[a-z0-9]+-[^"]+)"', board, re.I):
        url = m.group(1)
        seg = url.split("/p/", 1)[-1]
        tail = re.sub(r"^[a-z0-9]+-", "", seg).rstrip("/")
        if tail == want_slug:
            if url.startswith("/"):
                url = f"https://{slug}.breezy.hr" + url
            return url, None
    return None, "title_not_on_live_board"


def resolve_smartrecruiters(cand):
    """SmartRecruiters official public API; presence = active."""
    slug = slugify(cand["company"])
    if not slug:
        return None, "no_slug"
    data, err = fetch(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    if err:
        return None, "api_" + err
    time.sleep(1)
    try:
        postings = json.loads(data).get("content", [])
    except Exception:
        return None, "api_parse"
    want = norm(cand["title"])
    for p in postings:
        if norm(p.get("name")) == want:
            return "https://jobs.smartrecruiters.com/" + slug + "/" + str(p.get("id")), None
    return None, "title_not_in_live_api"


RESOLVERS = {
    "breezy.hr": resolve_breezy,
    "smartrecruiters.com": resolve_smartrecruiters,
}



def job_geo_verdict(page, text):
    """Egypt eligibility from the job's own location data, not boilerplate.

    Order: JSON-LD jobLocation (decisive) -> header region near the title
    -> explicitly global remote without restrictions. A country list,
    office footer or 'our locations' section never counts.
    """
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        if '"EG"' in block or EGYPT.search(block):
            if "jobLocation" in block or "PostalAddress" in block:
                return "pass"
        m = re.search(r'"addressCountry"\s*:\s*"([A-Z]{2})"', block)
        if m and m.group(1) != "EG" and "jobLocation" in block:
            return "reject"
    head = text[:2000]
    if EGYPT.search(head):
        return "pass"
    if REMOTE_GLOBAL.search(head) and not REMOTE_RESTRICT.search(text):
        return "pass"
    return "reject"

def extract_evidence(page, text):
    """Display evidence from the verified original page (for the fold step)."""
    geo, schedule, work = "", "", ""
    m = re.search(r'"employmentType"\s*:\s*"([^"]+)"', page)
    if m:
        schedule = m.group(1).replace("_", " ").title()
    city = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', page)
    country = re.search(r'"addressCountry"\s*:\s*"([A-Z]{2})"', page)
    if city and country:
        geo = city.group(1) + ", " + country.group(1)
    elif EGYPT.search(text[:2000]):
        geo = "Egypt"
    elif REMOTE_GLOBAL.search(text[:2000]):
        geo = "Remote (worldwide)"
    head = text[:3000].lower()
    if "hybrid" in head:
        work = "Hybrid"
    elif "remote" in head:
        work = "Remote"
    elif "on-site" in head or "onsite" in head or "on site" in head:
        work = "On-site"
    dm = DUTIES.search(text)
    duties = text[max(0, dm.start() - 40): dm.start() + 160].strip() if dm else ""
    return {"geo": geo, "work": work, "schedule": schedule, "duties": duties}


def check_original(title, company, url):
    """Fetch the original page once; apply live/match/active/geography/duties.
    Returns (ok, why, evidence)."""
    page, err = fetch(url)
    if err:
        return False, "original_" + err, None
    text = re.sub(r"<[^>]+>", " ", page)
    text = html.unescape(re.sub(r"\s+", " ", text))
    if EXPIRY.search(text):
        return False, "expiry_notice", None
    if norm(title) not in norm(text):
        return False, "title_mismatch_on_original", None
    if company and norm(company) not in norm(text):
        return False, "company_mismatch_on_original", None
    geo = job_geo_verdict(page, text)
    if geo == "reject":
        return False, "geography_unknown_or_not_egypt_eligible", None
    if not (DUTIES.search(text) and len(text) > 800):
        return False, "no_duties_evidence", None
    return True, "ok", extract_evidence(page, text)


def main():
    prev_data = None
    args = sys.argv[1:]
    if "--prev-data" in args:
        prev_data = args[args.index("--prev-data") + 1]
    if "--max-fetches" in args:
        FETCHES["max"] = int(args[args.index("--max-fetches") + 1])

    store = {"candidates": []}
    if os.path.exists(STORE):
        store = json.load(open(STORE, encoding="utf-8"))

    promotions, kept_internal, dropped_published, unchecked_published = [], [], [], []

    # Pass 1: re-check previously published Jooble rows (site integrity first;
    # re-checks are never preempted by new candidates, so previously published
    # cards cannot be starved by candidate growth).
    published = []
    if prev_data and os.path.exists(prev_data):
        try:
            prev = json.load(open(prev_data, encoding="utf-8"))
            published = [j for j in prev.get("jobs", []) if j.get("source") == "Jooble"]
        except Exception as e:
            print("jooble_promote: prev-data unreadable:", type(e).__name__)
    rechecked_ids = set()
    for row in published:
        jid = (row.get("canonical_key") or "").split(":", 1)[-1]
        rechecked_ids.add(jid)
        if STOP["hit"]:
            unchecked_published.append({"jooble_id": jid, "reason": "stopped_" + STOP["hit"]})
            continue
        if FETCHES["n"] >= FETCHES["max"]:
            # NOT proven expired: capacity-unchecked only.
            unchecked_published.append({"jooble_id": jid, "reason": "recheck_capacity_exceeded"})
            continue
        url = row.get("url") or ""
        ok, why, ev = check_original(row.get("title") or "", row.get("company") or "", url)
        time.sleep(1)
        if ok:
            promotions.append({
                "id": jid, "title": row["title"], "employer": row.get("company") or "",
                "original_url": url, "first_seen": row.get("first_seen") or None,
                "geo": ev["geo"], "work": ev["work"], "schedule": ev["schedule"],
                "duties": ev["duties"], "recheck": True,
            })
        elif "budget_exhausted" in why or "stopped_after" in why:
            unchecked_published.append({"jooble_id": jid, "reason": why})
        else:
            dropped_published.append({"jooble_id": jid, "reason": why})

    # Pass 2: new eligible candidates from today's store.
    eligible = [c for c in store.get("candidates", [])
                if c.get("title_relevant") and not c.get("intermediary_source")
                and c.get("jooble_id") not in rechecked_ids]
    for c in eligible:
        if STOP["hit"]:
            kept_internal.append({"jooble_id": c["jooble_id"], "reason": "stopped_" + STOP["hit"]})
            continue
        res = RESOLVERS.get((c.get("source_domain") or "").lower())
        if not res:
            kept_internal.append({"jooble_id": c["jooble_id"],
                                  "reason": "no_resolver_for_" + (c.get("source_domain") or "unknown")})
            continue
        url, err = res(c)
        time.sleep(1)
        if not url:
            kept_internal.append({"jooble_id": c["jooble_id"], "reason": err})
            continue
        ok, why, ev = check_original(c["title"], c["company"], url)
        time.sleep(1)
        if not ok:
            kept_internal.append({"jooble_id": c["jooble_id"], "reason": why})
            continue
        promotions.append({
            "id": c["jooble_id"], "title": c["title"], "employer": c["company"],
            "original_url": url, "first_seen": None,
            "geo": ev["geo"], "work": ev["work"], "schedule": ev["schedule"],
            "duties": ev["duties"], "recheck": False,
        })
    out = {"run_at": NOW.isoformat(), "vertical": store.get("vertical"),
           "eligible": len(eligible), "promoted": len(promotions),
           "kept_internal": len(kept_internal),
           "published_rechecked": len(rechecked_ids),
           "dropped_published": dropped_published,
           "unchecked_published": unchecked_published,
           "fetches_used": FETCHES["n"], "fetches_max": FETCHES["max"],
           "promotions": promotions, "internal_reasons": kept_internal,
           "stopped_by": STOP["hit"]}
    with open(PROMO, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"jooble_promote[{store.get('vertical')}]: eligible={len(eligible)} "
          f"promoted={len(promotions)} kept_internal={len(kept_internal)} "
          f"rechecked={len(rechecked_ids)} dropped={len(dropped_published)} "
          f"unchecked={len(unchecked_published)} "
          f"fetches={FETCHES['n']}/{FETCHES['max']} stopped_by={STOP['hit']}")
    for p in promotions:
        print(f"  PROMOTED: {p['title']} @ {p['employer']} -> {p['original_url'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
