#!/usr/bin/env python3
"""Build the three Career Intelligence static sites from the shared template.
Evolves the currently deployed static frontends (decision recorded 2026-11-21:
keeps the existing single-file static architecture and integration contract;
does not claim backend/pipeline execution)."""
import html as _html
import json, os, pathlib, re

ROOT = pathlib.Path(__file__).parent
TEMPLATE = (ROOT / "template.html").read_text(encoding="utf-8")

SITES = {
    "bapm": {
        "key": "bapm",
        "sub_en_rel": "Business Analysis and Product Management jobs and internships, each individually checked against its live source on the snapshot date. Contradictory or missing evidence is marked, never guessed.",
        "sub_ar_rel": "وظائف وتدريبات تحليل الأعمال وإدارة المنتجات، كل إعلان فيها اتراجع مقابل مصدره الحي يوم اللقطة. أي دليل ناقص أو متضارب متوضح، ومش متخمن.",
        "repo": "ba-pm-jobs-internships",
        "hue": "245", "prod_url": "https://pm-ba-opportunities.vercel.app", "fav": "%237c6cf0", "theme_color": "#0d0f16",
        "title_en": "BA & PM Jobs & Internships",
        "title_ar": "وظائف وتدريبات BA وPM",
        "brand_en": "BA & PM Opportunities",
        "brand_ar": "فرص BA وPM",
        "sub_en_pvm": "Business Analysis and Product Management jobs and internships collected from established public job APIs. Each card shows whether its listing was individually verified; unverified means fetched, not confirmed.",
        "sub_ar_pvm": "وظائف وتدريبات تحليل الأعمال وإدارة المنتجات متجمّعة من واجهات برمجية عامة موثقة المصدر. كل كارت يوضح هل إعلانه اتحقق منه على مستوى الإعلان نفسه؛ غير الموثق يعني جُلب ولم يُؤكَّد بعد.",
        "sub_en": "Business Analysis and Product Management jobs and internships in Egypt, the Gulf, the USA and Europe. Every listing was checked by hand when captured; contradictory or missing evidence is marked, never guessed.",
        "sub_ar": "وظائف وتدريبات تحليل الأعمال وإدارة المنتجات في مصر والخليج وأمريكا وأوروبا. كل إعلان اتراجع يدويًا وقت رصده؛ أي دليل ناقص أو متضارب متوضح، ومش متخمن.",
        "roles": ["Product Manager", "Product Owner", "Business Analyst", "Project Manager", "Program Manager", "Product Designer", "UX", "Scrum Master", "Intern", "Senior"],
    },
    "ai": {
        "key": "ai",
        "sub_en_rel": "Applied AI, automation, agents and vibe coding jobs and internships, judged by duties not titles; each individually checked against its live source on the snapshot date.",
        "sub_ar_rel": "وظائف وتدريبات الذكاء الاصطناعي التطبيقي والأتمتة والـvibe coding، بتتقاس بمهامها الفعلية مش بعناوينها؛ وكل إعلان اتحقق منه من مصدره الحي يوم اللقطة.",
        "repo": "ai-automation-jobs-internships",
        "hue": "168", "prod_url": "https://ai-automation-jobs-internships.vercel.app", "fav": "%233fbf8f", "theme_color": "#0d0f16",
        "title_en": "AI Applications, Automation & Vibe Coding Jobs & Internships",
        "title_ar": "وظائف وتدريبات تطبيقات الذكاء الاصطناعي والأتمتة",
        "brand_en": "Applied AI Opportunities",
        "brand_ar": "فرص الذكاء التطبيقي",
        "sub_en_pvm": "Applied AI, automation, agents and vibe coding jobs and internships collected from established public job APIs, judged by duties not titles. Each card shows whether its listing was individually verified.",
        "sub_ar_pvm": "وظائف وتدريبات الذكاء الاصطناعي التطبيقي والأتمتة والوكلاء والـvibe coding متجمّعة من واجهات برمجية عامة موثقة المصدر، بتتقاس بمهامها الفعلية مش بعناوينها. كل كارت يوضح هل إعلانه اتحقق منه.",
        "sub_en": "Applied AI, automation, agents and vibe coding jobs and internships in Egypt, the Gulf, the USA and Europe. Roles are judged by their actual duties, not their titles; every listing was checked by hand when captured.",
        "sub_ar": "وظائف وتدريبات الذكاء الاصطناعي التطبيقي والأتمتة والوكلاء والـvibe coding في مصر والخليج وأمريكا وأوروبا. الأدوار بتتقاس بمهامها الفعلية مش بعناوينها؛ وكل إعلان اتراجع يدويًا وقت رصده.",
        "roles": ["AI Engineer", "Automation", "n8n", "Prompt", "AI Agent", "Workflow", "Machine Learning", "No-Code", "Intern", "Junior"],
    },
    "marketing": {
        "key": "marketing",
        "sub_en_rel": "Digital, performance, content, brand and growth marketing jobs and internships, each individually checked against its live source on the snapshot date. Contradictory or missing evidence is marked, never guessed.",
        "sub_ar_rel": "وظائف وتدريبات التسويق الرقمي والأداء والمحتوى والبراند والنمو، كل إعلان فيها اتراجع مقابل مصدره الحي يوم اللقطة. أي دليل ناقص أو متضارب متوضح، ومش متخمن.",
        "repo": "marketing-jobs-internships",
        "hue": "18", "prod_url": "https://marketing-jobs-internships.vercel.app", "fav": "%23f0764a", "theme_color": "#0d0f16",
        "title_en": "Marketing Jobs & Internships",
        "title_ar": "وظائف وتدريبات التسويق",
        "brand_en": "Marketing Opportunities",
        "brand_ar": "فرص التسويق",
        "sub_en_pvm": "Digital, performance, content, brand and growth marketing jobs and internships collected from established public job APIs. Each card shows whether its listing was individually verified; unverified means fetched, not confirmed.",
        "sub_ar_pvm": "وظائف وتدريبات التسويق الرقمي والأداء والمحتوى والبراند والنمو متجمّعة من واجهات برمجية عامة موثقة المصدر. كل كارت يوضح هل إعلانه اتحقق منه؛ غير الموثق يعني جُلب ولم يُؤكَّد بعد.",
        "sub_en": "Digital, performance, content, brand and growth marketing jobs and internships in Egypt, the Gulf and beyond. Every listing was checked by hand when captured; contradictory or missing evidence is marked, never guessed.",
        "sub_ar": "وظائف وتدريبات التسويق الرقمي والأداء والمحتوى والبراند والنمو في مصر والخليج وأبعد. كل إعلان اتراجع يدويًا وقت رصده؛ أي دليل ناقص أو متضارب متوضح، ومش متخمن.",
        "roles": ["Marketing Manager", "Marketing Specialist", "Digital Marketing", "Social Media", "Performance", "Brand", "Content", "Growth", "SEO", "Intern"],
    },
}

VERCEL_JSON = {
    "headers": [
        {
            "source": "/(.*)",
            "headers": [
                {"key": "X-Content-Type-Options", "value": "nosniff"},
                {"key": "X-Frame-Options", "value": "DENY"},
                {"key": "Referrer-Policy", "value": "no-referrer"},
                {"key": "Permissions-Policy", "value": "camera=(), microphone=(), geolocation=()"},
            ],
        }
    ]
}

# --- Shared rendering core (F8 build-safety fix, 2026-11-22) ---
# Every placeholder with its REQUIRED occurrence count in the pristine template
# and its output encoding. __DATA__ and __SITE_CONFIG__ embed JSON inside a
# <script> block; the rest land in HTML text or double-quoted attribute
# contexts (title/meta/brand appear multiple times by design - exact counts
# below are the contract, so a missing OR duplicated token fails the build
# before anything is written).
RENDER_TOKENS = {
    "__TITLE_EN__": (4, "html"),
    "__META_DESC__": (3, "html"),
    "__THEME_COLOR__": (1, "html"),
    "__FAV_HUE__": (1, "html"),
    "__PROD_URL__": (1, "html"),
    "__BRAND_EN__": (1, "html"),
    "__SITE_CONFIG__": (1, "json"),
    "__DATA__": (1, "json"),
}
_RENDER_RE = re.compile("|".join(re.escape(t) for t in RENDER_TOKENS))


def json_for_script(obj):
    """JSON safe to embed inside a <script> block.

    '<' becomes \<u003c so external text can never terminate the script
    element; U+2028/U+2029 are escaped for classic-script parsing. JSON.parse
    restores every string byte-for-byte, so page logic sees the original data.
    """
    return (json.dumps(obj, ensure_ascii=False)
            .replace("<", "\\u003c")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def render_page(template, html_values, json_values):
    """Validate the pristine template, then substitute every token in ONE pass.

    - Token counts must match RENDER_TOKENS exactly, so a template edit that
      drops or duplicates a required placeholder fails loudly instead of
      silently shipping a page without its data.
    - HTML-context values get full text/attribute escaping; JSON-context
      values get script-safe JSON. Substituted content is never rescanned, so
      a value literally containing a token string is left untouched.
    Raises ValueError on any token-count mismatch (caller must not write).
    """
    bad = {t: template.count(t) for t, (want, _kind) in RENDER_TOKENS.items()
           if template.count(t) != want}
    if bad:
        raise ValueError("template token mismatch (token: found occurrences): %r" % bad)
    table = {}
    for tok, (_want, kind) in RENDER_TOKENS.items():
        if kind == "html":
            table[tok] = _html.escape(str(html_values[tok]), quote=True)
        else:
            table[tok] = json_for_script(json_values[tok])
    return _RENDER_RE.sub(lambda m: table[m.group(0)], template)


def write_atomic(path, text):
    """Write via a same-directory temp file + rename so a failure never
    truncates or replaces the previously published file."""
    path = pathlib.Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def html_values_for(cfg, sub_key):
    return {
        "__TITLE_EN__": cfg["title_en"],
        "__META_DESC__": cfg[sub_key],
        "__THEME_COLOR__": cfg["theme_color"],
        "__FAV_HUE__": cfg["fav"],
        "__PROD_URL__": cfg["prod_url"],
        "__BRAND_EN__": cfg["brand_en"],
    }


def build(site):
    cfg = SITES[site]
    data = json.loads((ROOT / "data" / f"{site}.json").read_text(encoding="utf-8"))
    # Render and validate EVERYTHING before touching the output directory.
    out = render_page(TEMPLATE, html_values_for(cfg, "sub_en"),
                      {"__SITE_CONFIG__": cfg, "__DATA__": data})
    vercel = json.dumps(VERCEL_JSON, indent=2)
    dist = ROOT.parent / "dist" / cfg["repo"]
    dist.mkdir(parents=True, exist_ok=True)
    write_atomic(dist / "index.html", out)
    write_atomic(dist / "vercel.json", vercel)
    print(f"built {cfg['repo']}: {len(data['jobs'])} records, {len(out)} bytes")

if __name__ == "__main__":
    for s in SITES:
        build(s)
