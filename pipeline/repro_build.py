import json, pathlib, hashlib, sys
ROOT = pathlib.Path(__file__).parent
TEMPLATE = (ROOT / "template.html").read_text(encoding="utf-8")
g = {"__name__": "x", "__file__": str(ROOT / "build.py")}
exec((ROOT / "build.py").read_text(encoding="utf-8"), g)
SITES = g["SITES"]
render_page = g["render_page"]
write_atomic = g["write_atomic"]
html_values_for = g["html_values_for"]
def build_rel(site, outdir):
    cfg = SITES[site]
    # v3: no job data is loaded or embedded. The page fetches its JSON at
    # runtime from the repo's `data` branch; build only renders the shell.
    # Render + validate fully BEFORE writing: any token mismatch raises here
    # and the previously published index.html stays byte-identical.
    out = render_page(TEMPLATE, html_values_for(cfg, "sub_en_rel"),
                      {"__SITE_CONFIG__": cfg})
    p = pathlib.Path(outdir); p.mkdir(parents=True, exist_ok=True)
    write_atomic(p / "index.html", out)
    return hashlib.sha256(out.encode()).hexdigest()[:16]
if __name__ == "__main__":
    site, outdir = sys.argv[1], sys.argv[2]
    print(site, build_rel(site, outdir))
