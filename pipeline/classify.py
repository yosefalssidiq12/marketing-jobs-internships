#!/usr/bin/env python3
"""Career pipeline classifier v3 — title-gated, duties/context classification.
Assigns primary_site (one per record, per the directive's one-primary-site rule) plus also_sites
for overlap transparency. Overlap is reported, never summed. Rules are TUNABLE DECISIONS.
Original Product scope includes Product Design/UXUI — classify from duties/context."""
import json, re, os

OUT = os.path.dirname(os.path.abspath(__file__))

AI_TOKEN = r"\bai\b|artificial intelligence|\bllm\b|agentic|machine learning|\bml (engineer|ops)\b|prompt engineer|gen ?ai\b|generative ai|\bnlp\b|ai agent|conversation(?:ai|al)|ai automation|vibe cod|\bgenai\b|\bchatbot\b|\bcopilot\b"
TITLE_RULES = {
  "marketing": [r"marketing", r"\bseo\b", r"content (market|strateg|writ|creat|lead|manag)", r"social media",
    r"growth (market|lead|manag|hack)", r"\bbrand\b", r"copywrit", r"email market", r"performance market",
    r"\bppc\b", r"paid (media|social|search)", r"demand gen", r"digital market", r"media buyer",
    r"marketing operations", r"\bcmo\b", r"communications (manag|lead|strateg)"],
  "bapm": [r"product manag", r"product own", r"business analyst", r"project manag", r"program manag",
    r"product lead", r"scrum master", r"product strateg", r"head of product", r"\bcpo\b",
    r"product director", r"agile coach", r"product operat", r"product design", r"ux ui", r"ux/ui"],
  "ai": [AI_TOKEN],
}
# automation counts for ai ONLY with an explicit AI token in the same title (QA F2)
AI_AUTOMATION = re.compile(r"automation (engineer|lead|manag|specialist|architect)", re.I)
NON_AI_AUTOMATION = re.compile(r"test automation|qa automation|systems? automation|it automation|devops|jamf|macos|infrastructure automation|network automation", re.I)
AI_NONBUILDER = re.compile(r"ai trainer|video annotation|data annotation|content producer|ai tutor|labeling", re.I)
MKT_EXCL = re.compile(r"recruiter|recruiting|recruitment|personalmarketing|talent acquisition|human resources|\bhr\b", re.I)
BAPM_RETAIL = re.compile(r"menswear|womenswear|apparel|fashion product|retail product|merchandis", re.I)
SALESY = re.compile(r"account executive|sales (representative|manager|director|engineer|lead|executive)"
  r"|business development|\bsdr\b|\bbdr\b|account manager|customer success|partnerships? (manager|lead)"
  r"|relationship manager|revenue", re.I)
BUILDER = re.compile(r"engineer|developer|architect|builder|scientist|analyst|automation|technical product"
  r"|deployment|solutions? engineer", re.I)
EXCL_AI = re.compile(r"research scientist|ml research|deep learning research|machine learning scientist|\bml scientist\b|ai researcher|research engineer", re.I)
DUTIES_REVIEW = re.compile(r"machine learning engineer|\bml engineer\b", re.I)
BAPM_TITLE = re.compile(r"product (manag|own|lead|strateg|director|operat|design)|business analyst"
  r"|project manag|program manag|scrum|agile|head of product|\bcpo\b", re.I)

def classify(j):
    t = j["title"].lower()
    hits = set()
    if any(re.search(p, t) for p in TITLE_RULES["marketing"]) and not MKT_EXCL.search(j["title"]):
        hits.add("marketing")
    if any(re.search(p, t) for p in TITLE_RULES["bapm"]) and not BAPM_RETAIL.search(j["title"]):
        hits.add("bapm")
    ai_hit = any(re.search(p, t) for p in TITLE_RULES["ai"])
    if AI_AUTOMATION.search(j["title"]) and re.search(AI_TOKEN, t):
        ai_hit = True
    if NON_AI_AUTOMATION.search(j["title"]) and not re.search(AI_TOKEN, t):
        ai_hit = False
    if AI_NONBUILDER.search(j["title"]):
        ai_hit = False
    if ai_hit and EXCL_AI.search(j["title"]):
        ai_hit = False
    if ai_hit:
        hits.add("ai")
    if SALESY.search(j["title"]):
        if not any(re.search(p, t) for p in TITLE_RULES["marketing"]):
            hits.discard("marketing")
        if "bapm" in hits and not BAPM_TITLE.search(t):
            hits.discard("bapm")
        if "ai" in hits and not BUILDER.search(j["title"]):
            hits.discard("ai")
    # one primary site per record; deterministic tie-break bapm > ai > marketing (tunable decision)
    primary, also = None, []
    for s in ("bapm", "ai", "marketing"):
        if s in hits:
            if primary is None: primary = s
            else: also.append(s)
    return primary, also

if __name__ == "__main__":
    d = json.load(open(os.path.join(OUT, "collected.json")))
    for coll in ("jobs", "archived"):
        for j in d[coll]:
            p, a = classify(j)
            j["primary_site"] = p
            j["also_sites"] = a
            if p == "ai" and DUTIES_REVIEW.search(j["title"]): j["duties_review_needed"] = True
            j["sites"] = ([p] if p else []) + a  # backward-compat view; primary is authoritative
    json.dump(d, open(os.path.join(OUT, "collected.json"), "w"), ensure_ascii=False, indent=1)
    import collections
    act = d["jobs"]
    prim = collections.Counter(j["primary_site"] for j in act if j["primary_site"])
    overlap = sum(1 for j in act if j["also_sites"])
    print("primary counts (unique, non-additive-safe):", dict(prim), "| overlap records:", overlap,
          "| unclassified:", sum(1 for j in act if not j["primary_site"]))
