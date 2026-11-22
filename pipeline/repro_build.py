import json, pathlib, hashlib, sys
ROOT = pathlib.Path(__file__).parent
TEMPLATE = (ROOT / "template.html").read_text(encoding="utf-8")
g = {"__name__": "x", "__file__": str(ROOT / "build.py")}
exec((ROOT / "build.py").read_text(encoding="utf-8"), g)
SITES = g["SITES"]
def build_rel(site, outdir):
    cfg = SITES[site]
    data = json.loads((ROOT / "data-release" / f"{site}.json").read_text(encoding="utf-8"))
    out = TEMPLATE
    out = out.replace("__TITLE_EN__", cfg["title_en"])
    out = out.replace("__META_DESC__", cfg["sub_en_rel"].replace('"', "'"))
    out = out.replace("__THEME_COLOR__", cfg["theme_color"])
    out = out.replace("__FAV_HUE__", cfg["fav"])
    out = out.replace("__PROD_URL__", cfg["prod_url"])
    out = out.replace("__BRAND_EN__", cfg["brand_en"])
    out = out.replace("__SITE_CONFIG__", json.dumps(cfg, ensure_ascii=False))
    out = out.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    p = pathlib.Path(outdir); p.mkdir(parents=True, exist_ok=True)
    (p / "index.html").write_text(out, encoding="utf-8")
    return hashlib.sha256(out.encode()).hexdigest()[:16]
if __name__ == "__main__":
    site, outdir = sys.argv[1], sys.argv[2]
    print(site, build_rel(site, outdir))
