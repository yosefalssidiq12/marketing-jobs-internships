#!/usr/bin/env python3
"""
jooble_fold.py — fold promoted Jooble records into the refresh.py output payload.

Runs INSIDE the data-publish job, after "Run refresh" and before the workflow's
publish-ready marker check. Mirrors refresh.py's emit_from_fresh record shape,
sort, counts and state semantics exactly, so the folded payload is
indistinguishable in contract from a refresh.py-produced one.

Atomicity contract:
  - DATA (data-release JSON) and STATE (pipeline-state.json) are only written
    after every promoted record validates. On any validation/structural error
    they are left byte-identical and the script still exits 0, with
    report.jooble.status = "failed" + reason, so the base publication proceeds.
  - REPORT (refresh-report.json) is always updated with an honest jooble
    section. Base (jobicy/arbeitnow) numbers in the report are never touched
    except counts.publishable, which is recomputed as len(jobs).

Status values: ok | zero | failed | stopped | skipped
  ok      - store existed, promotions non-empty, records folded
  zero    - pipeline ran fine but nothing qualified/promoted
  failed  - collect/promote/fold error (reason recorded)
  stopped - upstream challenge/circuit stop (no retries happened)
  skipped - no store present at all (e.g. collect never wrote; step skipped)

No secrets. No network. Logs print aggregates only.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone

CARD_KEYS = [
    "id", "title", "company", "region", "location", "workplace", "type",
    "posted", "source", "url", "note", "level", "applicants", "live",
    "first_seen", "first_seen_at", "status", "featured", "verified",
    "vlabel", "verification_method", "canonical_key", "elig",
    "link_status", "vlabel_ar", "new_vs_baseline",
]

AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
def ar_date(d): return d.translate(AR_DIGITS)

# Exact VLABELS text from refresh.py (method "bounded_content_check")
VL_EN = "Listing page live, title matched, no expiry notice {d}"
VL_AR = "صفحة الإعلان تعمل والعنوان مطابق وبلا إشعار انتهاء بتاريخ {ad}"

ELIG_PAT = re.compile(
    r"(eligible to work[^.]*|work authori[sz]ation[^.]*|must (?:be )?(?:based|located|residing) in[^.]*"
    r"|(?:us|usa|eu|emea|uk|canada)[- ](?:based|only)[^.]*|within[^.]*time ?zone[^.]*"
    r"|citizens? (?:of|only)[^.]*)", re.I)

def slug(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (s or "").lower())).strip("-")[:60]

def region(loc):
    l = (loc or "").lower()
    if re.search(r"egypt|cairo|alexandria|giza", l): return "Egypt"
    if re.search(r"uae|dubai|abu dhabi|emirates|sharjah", l): return "UAE"
    if re.search(r"saudi|riyadh|jeddah", l): return "Saudi Arabia"
    if re.search(r"qatar|kuwait|bahrain|oman|doha|gulf|mena", l): return "Other Gulf"
    if re.search(r"canada|toronto|vancouver|montreal", l): return "USA & Canada"
    if re.search(r"\busa\b|united states|\bus\b|new york|san francisco|austin|boston|seattle|chicago", l): return "USA"
    if re.search(r"europe|emea|germany|berlin|munich|\buk\b|london|netherlands|amsterdam|spain|france|poland|austria|vienna|ireland|dublin|sweden|portugal|lisbon|italy|czech|romania|hungary|switzerland|zurich|belgium|denmark|finland|norway", l): return "Europe"
    if re.search(r"remote|anywhere|worldwide|global", l): return "Worldwide"
    return "Other"

def workplace_from(schedule, location):
    t = ((schedule or "") + " " + (location or "")).lower()
    if "remote" in t or "anywhere" in t: return "Remote"
    if "hybrid" in t: return "Hybrid"
    if "on-site" in t or "onsite" in t or "on site" in t: return "Onsite"
    return "Not stated"

def jtype_from(schedule):
    t = (schedule or "").lower()
    for k, v in [("full", "Full-time"), ("part", "Part-time"), ("contract", "Contract"),
                 ("intern", "Internship"), ("temporary", "Temporary"), ("freelance", "Contract")]:
        if k in t: return v
    return "Not stated"

def sid_for(canonical_key):
    return hashlib.sha1(canonical_key.encode("utf-8")).hexdigest()[:16]

def build_card(rec, today, prev_first_seen):
    """rec: one promotions[] record. Mirrors refresh.py emit_from_fresh."""
    company = (rec.get("employer") or "").strip()
    title = (rec.get("title") or "").strip()
    url = (rec.get("original_url") or "").strip()
    jid = (rec.get("id") or "").strip()
    if not (company and title and url and jid):
        raise ValueError("promoted record missing employer/title/original_url/id")
    if not url.startswith("https://"):
        raise ValueError("original_url not https")
    canonical = "jooble:" + jid
    source_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    card_id = (slug(title + " " + company) + "-" + source_id)[:90]
    location = (rec.get("geo") or "").strip() or "Not stated"
    evidence = " ".join(str(rec.get(k) or "") for k in ("geo", "work", "duties", "title"))
    work_text = (rec.get("schedule") or "") + " " + (rec.get("work") or "")
    first_seen = prev_first_seen or today
    return {
        "id": card_id, "title": title, "company": company,
        "region": region(location), "location": location,
        "workplace": workplace_from(work_text, location),
        "type": jtype_from(rec.get("schedule")),
        "posted": None,  # Jooble "updated" is record refresh, not posting age
        "source": "Jooble", "url": url,
        "note": None, "level": None, "applicants": None, "live": False,
        "first_seen": first_seen[:10],
        "first_seen_at": (first_seen + "T00:00").replace("T", " ")[:16],
        "status": "new_update", "featured": False, "verified": True,
        "vlabel": VL_EN.format(d=today),
        "verification_method": "bounded_content_check",
        "canonical_key": canonical,
        "elig": ("See posting" if ELIG_PAT.search(evidence) else "Unknown"),
        "link_status": None,
        "vlabel_ar": VL_AR.format(ad=ar_date(today)),
        "new_vs_baseline": True,
    }

def validate_card(c):
    if list(c.keys()) != CARD_KEYS:
        raise ValueError("card key order/set mismatch")
    for k in CARD_KEYS:
        if k not in c: raise ValueError("missing key " + k)

def sort_key(x):
    return ((x.get("posted") or x.get("first_seen") or ""), x["id"])

def _atomic_dump(obj, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--promotions")          # may not exist
    ap.add_argument("--store")               # for requests/unique meta
    ap.add_argument("--status", default="skipped")  # ok|zero|failed|stopped from workflow
    ap.add_argument("--status-reason", default="")
    ap.add_argument("--date", required=True)  # TODAY from workflow env
    a = ap.parse_args()

    today = a.date
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    payload = json.loads(open(a.data, encoding="utf-8").read())
    state = json.loads(open(a.state, encoding="utf-8").read())
    report = json.loads(open(a.report, encoding="utf-8").read())

    meta_req = meta_uniq = None
    try:
        if a.store:
            st = json.loads(open(a.store, encoding="utf-8").read())
            meta_req = st.get("meta", {}).get("requests_used")
            meta_uniq = st.get("meta", {}).get("unique")
    except Exception:
        pass

    jooble = {"status": a.status, "reason": a.status_reason or None,
              "requests": meta_req, "unique": meta_uniq,
              "eligible": None, "promoted": 0, "folded": 0}

    promos, promo_meta = [], {}
    if a.status == "ok":
        try:
            raw = json.loads(open(a.promotions, encoding="utf-8").read())
            if isinstance(raw, dict):
                promos = raw.get("promotions", [])
                promo_meta = raw
            else:
                promos = raw
        except Exception as e:
            jooble["status"] = "failed"; jooble["reason"] = "promotions unreadable: " + type(e).__name__
            promos = None

    dropped = promo_meta.get("dropped_published") or []
    unchecked = promo_meta.get("unchecked_published") or []
    jooble["eligible"] = promo_meta.get("eligible")
    jooble["dropped"] = len(dropped)
    if dropped:
        jooble["dropped_reasons"] = [{"jooble_id": d.get("jooble_id"), "reason": d.get("reason")} for d in dropped]
    jooble["unchecked"] = len(unchecked)
    if unchecked:
        # Capacity/transient only: NOT proven expired.
        jooble["unchecked_reasons"] = [{"jooble_id": d.get("jooble_id"), "reason": d.get("reason")} for d in unchecked]
    if promo_meta.get("stopped_by") and not promos:
        a.status = "stopped"
        jooble["status"] = "stopped"
        jooble["reason"] = "upstream " + str(promo_meta["stopped_by"])

    if a.status == "ok" and promos is not None and not promos and not dropped and not unchecked:
        jooble["status"] = "zero"

    if jooble["status"] == "ok" and promos:
        try:
            by_ck = {j.get("canonical_key"): j for j in payload["jobs"]}
            existing_ids = {j.get("id") for j in payload["jobs"]}
            cards, updates = [], 0
            for rec in promos:
                card = build_card(rec, today, rec.get("first_seen"))
                prev_row = by_ck.get(card["canonical_key"])
                if prev_row is not None:
                    # Re-verified today by promote: refresh the label date in place
                    # (mirrors the jobicy prev-row re-verify branch in refresh.py),
                    # keep first_seen and every other field verbatim.
                    prev_row["vlabel"] = card["vlabel"]
                    prev_row["vlabel_ar"] = card["vlabel_ar"]
                    prev_row["verified"] = True
                    updates += 1
                else:
                    while card["id"] in existing_ids:
                        card["id"] = card["id"] + "-" + hashlib.sha1(card["canonical_key"].encode()).hexdigest()[:6]
                    validate_card(card)
                    cards.append(card)
            # all validated; now mutate
            for card in cards:
                payload["jobs"].append(card)
            new_by_ck = {c["canonical_key"]: c for c in cards}
            for rec in promos:
                canonical = "jooble:" + (rec.get("id") or "").strip()
                url = (rec.get("original_url") or "").strip()
                if not url.startswith("https://"): continue
                sid = sid_for(canonical)
                prev = state.get(sid) or {}
                fs = prev.get("first_seen")
                if not fs:
                    row = by_ck.get(canonical) or new_by_ck.get(canonical) or {}
                    fsa = (row.get("first_seen_at") or "").replace(" ", "T")
                    fs = (fsa + ":00+00:00") if fsa else now_iso
                state[sid] = {
                    "first_seen": fs,
                    "last_seen": now_iso,
                    "url": url,
                    "last_verified": today,
                    "verification": "bounded_content_check",
                }
            payload["jobs"].sort(key=sort_key, reverse=True)
            _atomic_dump(payload, a.data)
            _atomic_dump(state, a.state)
            jooble["promoted"] = len(promos)
            jooble["folded"] = len(cards)
            jooble["reverified"] = updates
            if not cards and not updates and not dropped and not unchecked:
                jooble["status"] = "zero"; jooble["reason"] = "promotions empty after dedup"
        except Exception as e:
            jooble["status"] = "failed"
            jooble["reason"] = "fold error: " + type(e).__name__ + ": " + str(e)[:120]
            jooble["promoted"] = 0; jooble["folded"] = 0
    elif jooble["status"] == "ok":
        jooble["promoted"] = len(promos or [])

    # report: recompute publishable from actual payload; add jooble section
    report.setdefault("counts", {})["publishable"] = len(payload["jobs"])
    report["jooble"] = jooble
    _atomic_dump(report, a.report)

    print("jooble fold: status=%s promoted=%s folded=%s publishable=%d"
          % (jooble["status"], jooble.get("promoted"), jooble.get("folded"), len(payload["jobs"])))
    if jooble.get("reason"): print("jooble fold reason: " + str(jooble["reason"]))

if __name__ == "__main__":
    main()
