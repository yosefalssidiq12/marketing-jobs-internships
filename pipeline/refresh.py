#!/usr/bin/env python3
"""refresh.py - one-site refresh orchestrator for the Career static sites.

Run order: collect (remotive excluded) -> classify -> verify -> merge/carry-forward
-> release-schema JSON -> rebuild index.html via the byte-proven release recipe.

Honesty policy (owner directive, confirmed 2026-11-22):
- publish = verified-only; unverified records never publish.
- verify.py is the only writer of fresh verification (jobicy official status
  endpoint, or bounded content check: HTTP 200 AND title words AND no expiry
  notice - never a bare 200).
- Carried rows keep their ORIGINAL vlabel/vlabel_ar/verification_method and the
  original verification date inside the label, verbatim. Carried data is never
  presented as newly verified.
- A row is carried only while (a) it still appears in the fresh fetch (or, for
  jobicy, the official status endpoint still reports it active) and (b) its
  verification date is at most CARRY_TTL_DAYS old.
- Grace (accepted 2026-11-22): a non-jobicy row that VANISHES from a capped or
  rotating feed is carried until UTC midnight of its original verification date
  (CARRY_TTL_DAYS = 0), keeping that date and method verbatim; prior-day carry
  is rejected and archived as stale. Vanished is not closed: only an explicit
  negative signal (jobicy non-active status, or a current-run positive expiry
  notice) removes a row same-day. Otherwise a row is excluded from the
  publishable inventory and recorded in the run report as archived.
- Relevance: fresh rows need duties evidence in the description, or an
  adjudication.resolved_relevant entry. adjudication.excluded_mismatch never
  publishes (duties, not titles, qualify listings).
- Remotive is excluded from collection (standing source policy).
"""
import json, os, re, sys, datetime, importlib.util, collections

OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, OUT)
CARRY_TTL_DAYS = 0  # STRICT (QA 2026-11-22): publish = verified THIS run only.
# Carried rows are same-day-only by construction; older unverified rows drop out
# (archived with reason) rather than being presented under a stale check date.
# A dated carry-forward with distinct UI labeling is a separate user decision.
NOW = datetime.datetime.now(datetime.timezone.utc)
TODAY = NOW.date().isoformat()
# F3 (QA 2026-11-22): every run date is UTC. Same-day carry compares the UTC
# label date with the UTC run date, so a row checked late in the Cairo evening
# stays carriable until UTC midnight (up to ~2h into the next Cairo day) and is
# archived after that (CARRY_TTL_DAYS = 0). Carried labels always keep their
# original check date verbatim; nothing is ever presented as fresher than it is.
ACTIVE_STATUSES = {"active", "true", "1"}

AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
def ar_date(d): return d.translate(AR_DIGITS)

VLABELS = {
    "jobicy_status_endpoint": (
        "Active per Jobicy official status endpoint {d}",
        "نشط وفق نقطة الحالة الرسمية لـ Jobicy بتاريخ {ad}"),
    "bounded_content_check": (
        "Listing page live, title matched, no expiry notice {d}",
        "صفحة الإعلان تعمل والعنوان مطابق وبلا إشعار انتهاء بتاريخ {ad}"),
}
DATE_IN_LABEL = re.compile(r"(\d{4}-\d{2}-\d{2})\s*$")

def load_mod(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(OUT, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def slug(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (s or "").lower())).strip("-")[:60]

def label_date(row):
    m = DATE_IN_LABEL.search(row.get("vlabel") or "")
    if not m: return None
    try: return datetime.date.fromisoformat(m.group(1))
    except Exception: return None

def main():
    site = None
    repo_root = None
    dry_run = False
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--site": site = args[i + 1]
        elif a == "--repo-root": repo_root = args[i + 1]
        elif a == "--dry-run": dry_run = True
    assert site in ("bapm", "ai", "marketing"), "--site bapm|ai|marketing required"
    repo_root = repo_root or os.path.dirname(OUT)

    report = {"site": site, "run_at": NOW.isoformat(), "dry_run": dry_run,
              "fresh_verified": {}, "carried": [], "archived": [], "excluded": [],
              "errors": []}
    # Publish-ready marker: remove any stale marker left by an earlier
    # run, so only a marker created by THIS run after gate acceptance
    # can authorise publication.
    _old_marker = os.path.join(OUT, f".publish-ready-{site}")
    if os.path.exists(_old_marker):
        os.remove(_old_marker)

    # 1. collect (remotive gated off in collect.py unless CAREER_ENABLE_REMOTIVE=1)
    collect = load_mod("collect")
    collect.main()

    # 2. classify (same logic as classify.py __main__, in-process)
    classify = load_mod("classify")
    coll_path = os.path.join(OUT, "collected.json")
    d = json.load(open(coll_path))
    for coll in ("jobs", "archived"):
        for j in d[coll]:
            p, also = classify.classify(j)
            j["primary_site"] = p
            j["also_sites"] = also
            if p == "ai" and classify.DUTIES_REVIEW.search(j["title"]):
                j["duties_review_needed"] = True
            j["sites"] = ([p] if p else []) + also
    json.dump(d, open(coll_path, "w"), ensure_ascii=False, indent=1)

    # 3. verify (reads collected.json; jobicy status for all fresh jobicy rows,
    #    bounded content checks within caps)
    verify = load_mod("verify")
    verify.main()
    d = json.load(open(coll_path))
    dedup_losers = {l["id"]: l for l in (d.get("dedup_losers") or [])}  # F9: confirmed dedup losers
    dedup_loser_ck = {l["source"] + ":" + (l.get("source_id") or l["id"]): l for l in (d.get("dedup_losers") or []) if l.get("source")}

    # 3b. jobicy status for PREV-published jobicy rows absent from this fetch
    prev_path = os.path.join(OUT, "data-release", f"{site}.json")
    prev = json.load(open(prev_path)) if os.path.exists(prev_path) else {"jobs": []}
    prev_by_ck = {r["canonical_key"]: r for r in prev["jobs"]}
    fetched_urls = {j["url"] for coll in ("jobs", "archived") for j in d[coll]}
    fetched_ck = {j["source"] + ":" + (j.get("source_id") or j["id"]) for coll in ("jobs", "archived") for j in d[coll]}
    run_errors = list(d.get("errors") or [])
    missing_jobicy_ids = []
    for ck, r in prev_by_ck.items():
        src, _, sid = ck.partition(":")
        if src == "jobicy" and sid and ck not in fetched_ck:
            missing_jobicy_ids.append(sid)
    prev_jobicy_status = {}
    if missing_jobicy_ids:
        _nerr = len(verify.STATUS_ERRORS)
        prev_jobicy_status = verify.jobicy_status(missing_jobicy_ids)
        run_errors += verify.STATUS_ERRORS[_nerr:]
        report["prev_jobicy_status_checked"] = len(prev_jobicy_status)

    # 4. build publishable inventory
    bc = load_mod("build_candidates_helpers") if os.path.exists(os.path.join(OUT, "build_candidates_helpers.py")) else None
    adj = json.load(open(os.path.join(OUT, "adjudication.json"))) if os.path.exists(os.path.join(OUT, "adjudication.json")) else {}
    resolved = set(adj.get("resolved_relevant", []))
    excluded_mismatch = adj.get("excluded_mismatch", {})

    DUTY = {
     "bapm": re.compile(r"roadmap|requirements|stakeholder|user stor|backlog|prioriti[sz]|sprint|discovery|delivery|kpi|product strategy|business case", re.I),
     "ai": re.compile(r"llm|prompt|agent|rag\b|embeddings|fine-?tun|gen ?ai|automation pipeline|workflow automation|integrat", re.I),
     "marketing": re.compile(r"campaign|seo|content|brand|conversion|acquisition|retention|paid (?:media|social|search)|funnel|copy|audience|social media", re.I),
    }
    ELIG_PAT = re.compile(r"(eligible to work[^.]*|work authori[sz]ation[^.]*|must (?:be )?(?:based|located|residing) in[^.]*|(?:us|usa|eu|emea|uk|canada)[- ](?:based|only)[^.]*|within[^.]*time ?zone[^.]*|citizens? (?:of|only)[^.]*)", re.I)
    SRCNAME = {"jobicy": "Jobicy", "remotive": "Remotive", "arbeitnow": "Arbeitnow"}

    COUNTRY_REGION = {
        "eg": "Egypt", "egypt": "Egypt",
        "ae": "UAE", "uae": "UAE",
        "sa": "Saudi Arabia",
        "qa": "Other Gulf", "kw": "Other Gulf", "bh": "Other Gulf", "om": "Other Gulf",
        "ca": "USA & Canada", "canada": "USA & Canada",
        "us": "USA", "usa": "USA", "united states": "USA",
        "de": "Europe", "germany": "Europe", "hu": "Europe", "hungary": "Europe",
        "at": "Europe", "austria": "Europe", "cz": "Europe", "pl": "Europe", "poland": "Europe",
        "ro": "Europe", "se": "Europe", "fi": "Europe", "no": "Europe", "dk": "Europe",
        "be": "Europe", "ch": "Europe", "ie": "Europe", "es": "Europe", "fr": "Europe",
        "it": "Europe", "pt": "Europe", "nl": "Europe", "gb": "Europe", "uk": "Europe",
        "gr": "Europe", "bg": "Europe", "hr": "Europe", "sk": "Europe", "si": "Europe",
        "ee": "Europe", "lv": "Europe", "lt": "Europe",
    }

    def region(loc):
        l = (loc or "").lower()
        tail = l.rsplit(",", 1)[-1].strip() if "," in l else ""
        if tail in COUNTRY_REGION:
            return COUNTRY_REGION[tail]
        if re.search(r"egypt|cairo|alexandria|giza|zamalek|maadi|nasr city|new cairo|heliopolis|smart village|sheikh zayed|6th of october|mohandessin|dokki|helwan|mansoura|tanta|assiut|luxor|aswan|hurghada|sharm|,\s*eg\b", l): return "Egypt"
        if re.search(r"uae|dubai|abu dhabi|emirates|sharjah", l): return "UAE"
        if re.search(r"saudi|riyadh|jeddah", l): return "Saudi Arabia"
        if re.search(r"qatar|kuwait|bahrain|oman|doha|gulf|mena", l): return "Other Gulf"
        if re.search(r"canada|toronto|vancouver|montreal", l): return "USA & Canada"
        if re.search(r"\busa\b|united states|\bus\b|new york|san francisco|austin|boston|seattle|chicago", l): return "USA"
        if re.search(r"europe|emea|germany|berlin|munich|\buk\b|london|netherlands|amsterdam|spain|france|poland|austria|vienna|ireland|dublin|sweden|portugal|lisbon|italy|czech|romania|hungary|switzerland|zurich|belgium|denmark|finland|norway", l): return "Europe"
        if re.search(r"remote|anywhere|worldwide|global", l): return "Worldwide"
        return "Other"

    def workplace(j):
        t = (j.get("type_raw", "") + " " + j.get("location", "")).lower()
        if "remote" in t or "anywhere" in t: return "Remote"
        if "hybrid" in t: return "Hybrid"
        if "on-site" in t or "onsite" in t or "on site" in t: return "Onsite"
        return "Not stated"

    def jtype(j):
        t = (j.get("type_raw", "")).lower()
        for k, v in [("full", "Full-time"), ("part", "Part-time"), ("contract", "Contract"),
                     ("intern", "Internship"), ("temporary", "Temporary"), ("freelance", "Contract")]:
            if k in t: return v
        return "Not stated"

    jobs_out, seen_ids = [], set()
    def emit_from_fresh(j, method, ver_date, keep_id=None):
        jid = keep_id or (slug(j["title"] + " " + j["company"]) + "-" + (j.get("source_id") or j["id"][:8]))[:90]
        if jid in seen_ids: jid = jid + "-" + j["id"][:6]
        seen_ids.add(jid)
        en, ar = VLABELS[method]
        m = ELIG_PAT.search(j.get("desc") or "")
        return {
            "id": jid, "title": j["title"], "company": j["company"],
            "region": region(j.get("location")), "location": j.get("location") or "Not stated",
            "workplace": workplace(j), "type": jtype(j),
            "posted": (j.get("source_posted") or "")[:10] or None,
            "source": SRCNAME.get(j["source"], j["source"]), "url": j["url"],
            "note": None, "level": None, "applicants": None, "live": False,
            "first_seen": (j.get("first_seen") or "")[:10],
            "first_seen_at": (j.get("first_seen") or "").replace("T", " ")[:16],
            "status": "new_update", "featured": False, "verified": True,
            "vlabel": en.format(d=ver_date), "verification_method": method,
            "canonical_key": j["source"] + ":" + (j.get("source_id") or j["id"]),
            "elig": ("See posting" if m else "Unknown"),
            "link_status": ("active" if method == "jobicy_status_endpoint" else None),
            "vlabel_ar": ar.format(ad=ar_date(ver_date)),
            "new_vs_baseline": True,
        }

    def too_old(prev_row):
        ld = label_date(prev_row)
        if ld is None: return True
        return (NOW.date() - ld).days > CARRY_TTL_DAYS

    for j in d["jobs"]:
        if j.get("primary_site") != site: continue
        ck = j["source"] + ":" + (j.get("source_id") or j["id"])
        # relevance gate (fresh evidence or adjudication)
        jid_guess = (slug(j["title"] + " " + j["company"]) + "-" + (j.get("source_id") or j["id"][:8]))[:90]
        if jid_guess in excluded_mismatch or ck in excluded_mismatch:
            report["excluded"].append({"id": jid_guess, "reason": "excluded_duties_mismatch"})
            continue
        duties_ok = bool((j.get("desc") or "").strip()) and bool(DUTY[site].search(j["desc"]))
        adjudicated_ok = jid_guess in resolved or ck in resolved
        prev_row = prev_by_ck.get(ck)
        lv_date = (j.get("last_verified") or "")[:10]
        if j.get("jobicy_inactive") is not None:  # set for ANY non-active status, incl. empty
            # F2: explicit non-active status from the Jobicy endpoint - never publish.
            if prev_row:
                report["archived"].append({"id": prev_row["id"], "reason": "jobicy status endpoint: " + j["jobicy_inactive"]})
            continue
        # fresh requires EXPLICIT current-run success (verified_this_run set by verify.py),
        # not merely an inherited same-day last_verified from pipeline-state (reviewer F4)
        if j.get("verified_this_run") is True and lv_date and lv_date >= TODAY:
            ver = j["verification"] or ""
            method = "jobicy_status_endpoint" if ver.startswith("jobicy official status") else "bounded_content_check"
            if not (duties_ok or adjudicated_ok or prev_row is not None):
                report["excluded"].append({"id": jid_guess, "reason": "relevance_duties_unconfirmed"})
                continue
            ver_date = lv_date
            jobs_out.append(emit_from_fresh(j, method, ver_date, keep_id=(prev_row or {}).get("id")))
            report["fresh_verified"][method] = report["fresh_verified"].get(method, 0) + 1
        elif j.get("expired_this_run") is True and prev_row is not None:
            # F6 (QA 2026-11-22): a positive expiry notice recorded by THIS run's content check
            # (structured flag set only by verify.py in this run) archives before carry - the
            # content-check equivalent of F2's jobicy non-active status. The second loop's
            # archived-check prevents resurrection.
            report["archived"].append({"id": prev_row["id"], "reason": f"content check: expiry notice {TODAY}"})
        elif prev_row is not None and prev_row.get("verified"):
            if too_old(prev_row):
                report["archived"].append({"id": prev_row["id"], "reason": f"carried verification older than {CARRY_TTL_DAYS}d"})
            else:
                jobs_out.append(prev_row)  # verbatim carry: original label, method, date
                report["carried"].append(prev_row["id"])

    # prev-published rows absent from this fetch
    for ck, r in prev_by_ck.items():
        if any(o["canonical_key"] == ck for o in jobs_out): continue
        if any(a.get("id") == r["id"] for a in report["archived"]): continue
        loser = dedup_loser_ck.get(ck) or dedup_losers.get(r["id"])
        if loser:
            # F9: this prev row's fresh record merged into a confirmed duplicate at collect
            # time (explicit loser identity). Archive with a duplicate-of reason; ordinary
            # same-day vanished grace must not resurrect it. Other vanished rows are unaffected.
            report["archived"].append({"id": r["id"], "reason": "dedup duplicate of " + loser.get("survivor_url", loser["duplicate_of"]) + " (" + loser.get("via", "?") + ")"})
            continue
        src, _, sid = ck.partition(":")
        if src == "jobicy" and sid in prev_jobicy_status and str(prev_jobicy_status.get(sid)).lower() not in ACTIVE_STATUSES:
            report["archived"].append({"id": r["id"], "reason": "jobicy status endpoint: " + str(prev_jobicy_status.get(sid)).lower()})
        elif src == "jobicy" and str(prev_jobicy_status.get(sid)).lower() in ACTIVE_STATUSES:
            rr = dict(r)
            en, ar = VLABELS["jobicy_status_endpoint"]
            rr["vlabel"] = en.format(d=TODAY)
            rr["vlabel_ar"] = ar.format(ad=ar_date(TODAY))
            rr["verification_method"] = "jobicy_status_endpoint"
            rr["link_status"] = "active"
            rr["verified"] = True
            jobs_out.append(rr)
            report["fresh_verified"]["jobicy_status_endpoint"] = report["fresh_verified"].get("jobicy_status_endpoint", 0) + 1
        elif not too_old(r):
            jobs_out.append(r)
            report["carried"].append(r["id"])
        else:
            report["archived"].append({"id": r["id"], "reason": "absent from fresh fetch and verification stale"})

    jobs_out.sort(key=lambda x: ((x.get("posted") or x.get("first_seen") or ""), x["id"]), reverse=True)

    payload = {
        "updated": NOW.strftime("%Y-%m-%d %H:%M UTC"),
        "generated_at": NOW.isoformat(),
        "date": TODAY,
        "release_candidate": True,
        "verification_model": f"v6-auto-refresh-{TODAY} (carried rows keep their original verification date in vlabel)",
        "publishable_verified_only": True,
        "jobs": jobs_out,
    }
    report["counts"] = {"publishable": len(jobs_out), "prev": len(prev["jobs"]),
                        "fresh": sum(report["fresh_verified"].values()),
                        "carried": len(report["carried"]), "archived": len(report["archived"]),
                        "excluded": len(report["excluded"])}

    # --- F1 (QA 2026-11-22): fail-closed publication gate ---
    # Never auto-publish a degraded snapshot: on any breach, write the run report,
    # leave data-release/<site>.json and index.html untouched, and exit non-zero so
    # the workflow's commit step never runs and the last accepted snapshot stands.
    GATE_MIN_ABSOLUTE = 5     # never auto-publish fewer than 5 rows
    GATE_MIN_RATIO = 0.5      # never below 50% of the previous snapshot (prev >= 10)
    total_collected = sum(len(d[c]) for c in ("jobs", "archived"))
    fresh_count = sum(report["fresh_verified"].values())
    breaches = []
    if total_collected == 0:
        breaches.append("collection outage: 0 rows collected from every source")
    if run_errors:
        report["errors"] = run_errors
        summ = "; ".join(f"{e.get('source') or e.get('stage')}:{e.get('scope') or '-'} {e.get('error','')[:80]}" for e in run_errors[:3])
        breaches.append(f"{len(run_errors)} source error(s) recorded this run ({summ})")
    # Partial-source failure: an enabled source contributing zero collected rows is an
    # outage for that source even when totals stay above the other thresholds.
    EXPECTED_SOURCES = ("jobicy", "arbeitnow")  # remotive excluded by standing source policy
    src_counts = {}
    for c in ("jobs", "archived"):
        for j in d[c]:
            src_counts[j.get("source")] = src_counts.get(j.get("source"), 0) + 1
    for s in EXPECTED_SOURCES:
        if src_counts.get(s, 0) == 0:
            breaches.append(f"source outage: {s} contributed 0 collected rows")
    jrows = [j for j in d["jobs"] if j.get("source") == "jobicy"]
    if jrows and not any(j.get("verified_this_run") is True for j in jrows):  # current-run success only; inherited timestamps never satisfy this
        breaches.append(f"jobicy status check yielded 0 active rows out of {len(jrows)} (endpoint outage or mass closure)")
    # F5 (QA reviewer 2026-11-22): feed-level fresh-verification guard for every other ENABLED
    # source with a nonempty verifier-eligible pool (same d.jobs/source/primary_site predicate
    # as verify.py). Zero current-run successes is inconclusive (outage, bot block, or mass
    # closure) and preserves the prior snapshot; it never means the rows closed. Carried rows
    # never satisfy this: the pool is this run's fresh collection only, and verified_this_run
    # is set only by verify.py in this run. No per-selected-site requirement: the content-check
    # cap is shared across classified sites, so a per-site rule could fail on cap ordering alone.
    report["fresh_verified_by_source"] = {}
    for s in EXPECTED_SOURCES:
        if s == "jobicy":
            continue  # covered by the feed-level jobicy guard above
        pool = [j for j in d["jobs"] if j.get("source") == s and j.get("primary_site")]
        if not pool:
            continue  # no eligible records for this source: nothing to require
        ok_src = sum(1 for j in pool if j.get("verified_this_run") is True)
        report["fresh_verified_by_source"][s] = {"eligible": len(pool), "fresh_success": ok_src}
        if ok_src == 0:
            breaches.append(f"{s} verification inconclusive: 0 fresh successes out of {len(pool)} eligible rows (outage, bot block, or mass closure - prior snapshot preserved)")
    if fresh_count == 0:
        breaches.append("verification outage: 0 rows freshly verified this run")
    if len(prev["jobs"]) >= 10 and len(jobs_out) < GATE_MIN_RATIO * len(prev["jobs"]):
        breaches.append(f"catastrophic drop: publishable {len(jobs_out)} < 50% of prev {len(prev['jobs'])}")
    if len(jobs_out) < GATE_MIN_ABSOLUTE:
        breaches.append(f"absolute floor: publishable {len(jobs_out)} < {GATE_MIN_ABSOLUTE}")
    report["publication_gate"] = {"passed": not breaches, "breaches": breaches,
        "rules": {"min_absolute": GATE_MIN_ABSOLUTE, "min_ratio_vs_prev": GATE_MIN_RATIO,
                  "require_collection_nonempty": True, "require_fresh_verification": True,
                  "expected_sources_nonempty": list(EXPECTED_SOURCES)},
        "source_counts": src_counts}
    json.dump(report, open(os.path.join(OUT, "refresh-report.json"), "w"), ensure_ascii=False, indent=1)
    if breaches:
        # pipeline-state.json was staged to .next by collect/verify and is NOT promoted:
        # the accepted state remains byte-identical for the next run.
        report["state_staged_not_promoted"] = True
        json.dump(report, open(os.path.join(OUT, "refresh-report.json"), "w"), ensure_ascii=False, indent=1)
        print("PUBLICATION GATE FAILED - last accepted snapshot preserved, nothing written:")
        for b in breaches: print(" -", b)
        sys.exit(3)

    if not dry_run:
        _st = os.path.join(OUT, "pipeline-state.json")
        if os.path.exists(_st + ".next"):
            os.replace(_st + ".next", _st)  # promote staged state only after gate acceptance
        json.dump(payload, open(prev_path, "w"), ensure_ascii=False, indent=1)
        # Do NOT build index.html here. The daily path must never produce
        # or commit a frontend file. The frontend is built only by the
        # manual frontend-build job (pipeline/repro_build.py).
        # Publish-ready marker: created ONLY here, i.e. only after the
        # gate passed and the payload was written. The workflow's publish
        # step requires this marker and deletes it after checking.
        _marker = os.path.join(OUT, f".publish-ready-{site}")
        with open(_marker, "w", encoding="utf-8") as _f:
            _f.write(payload["generated_at"] + "\n")
        report["build_sha256_16"] = None
    json.dump(report, open(os.path.join(OUT, "refresh-report.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(report["counts"], indent=1))
    print("carried:", len(report["carried"]), "archived:", len(report["archived"]), "excluded:", len(report["excluded"]))

if __name__ == "__main__":
    main()
