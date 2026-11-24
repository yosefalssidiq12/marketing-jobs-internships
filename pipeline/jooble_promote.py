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

def check_original(cand, url):
    """Fetch the original page once; apply live/match/active/geography/duties."""
    page, err = fetch(url)
    if err:
        return False, "original_" + err
    text = re.sub(r"<[^>]+>", " ", page)
    text = html.unescape(re.sub(r"\s+", " ", text))
    if EXPIRY.search(text):
        return False, "expiry_notice"
    if norm(cand["title"]) not in norm(text):
        return False, "title_mismatch_on_original"
    if cand["company"] and norm(cand["company"]) not in norm(text):
        return False, "company_mismatch_on_original"
    geo = job_geo_verdict(page, text)
    if geo == "reject":
        return False, "geography_unknown_or_not_egypt_eligible"
    if not (DUTIES.search(text) and len(text) > 800):
        return False, "no_duties_evidence"
    return True, "ok"


def main():
    if not os.path.exists(STORE):
        print("jooble_promote: no internal store; nothing to do")
        return 0
    store = json.load(open(STORE, encoding="utf-8"))
    eligible = [c for c in store.get("candidates", [])
                if c.get("title_relevant") and not c.get("intermediary_source")]
    promotions, kept_internal = [], []
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
        ok, why = check_original(c, url)
        time.sleep(1)
        if not ok:
            kept_internal.append({"jooble_id": c["jooble_id"], "reason": why})
            continue
        promotions.append({
            "jooble_id": c["jooble_id"], "title": c["title"], "company": c["company"],
            "original_url": url, "source_domain": c["source_domain"],
            "verification": "original page live; company+title match; active on live "
                            "board/API; Egypt/remote location; duties evidence",
            "verified_at": NOW.isoformat(),
        })
    out = {"run_at": NOW.isoformat(), "vertical": store.get("vertical"),
           "eligible": len(eligible), "promoted": len(promotions),
           "kept_internal": len(kept_internal),
           "promotions": promotions, "internal_reasons": kept_internal,
           "stopped_by": STOP["hit"]}
    with open(PROMO, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"jooble_promote[{store.get('vertical')}]: eligible={len(eligible)} "
          f"promoted={len(promotions)} kept_internal={len(kept_internal)} "
          f"stopped_by={STOP['hit']}")
    for p in promotions:
        print(f"  PROMOTED: {p['title']} @ {p['company']} -> {p['original_url'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
