# Career site recurring refresh package (per repo, self-contained)

Status: CORRECTED 2026-11-22 (QA v2: fail-closed gate, explicit-active verification, current-run success markers, structured error propagation, staged state). Pushed to branch refresh-2026-11-22 of ba-pm-jobs-internships; ai/marketing replication pending acceptance. Branch runs are FULL real rehearsals writing only to the branch; merge to main + cron activation only after QA acceptance.

## Facts grounding free-policy + authority fit
- Public repos: standard GitHub-hosted runners free (docs.github.com billing page).
- Keyless public sources only: jobicy + arbeitnow. REMOTIVE STAYS EXCLUDED (standing policy) - collect.py must be patched to gate the remotive block before push (currently unconditional at the remotive for-loop).
- Pure stdlib Python (urllib). No secrets, no env keys. GITHUB_TOKEN is repo-scoped; each repo builds only its own site. No PAT needed.
- Scheduled workflows auto-disable after 60 days of repo inactivity (docs.github.com disable-and-enable-workflows, verified verbatim 2026-11-22). The workflow's own commits are expected to count as activity; if a repo ever goes 60 days with zero runs/commits, re-enable manually. Documented as continuity risk, monitored via run failure alerts.

## Freshness / carry-forward policy (from main, 02:21)
- Page-checked rows carry their ORIGINAL verified-at timestamp and method string verbatim. Never presented as newly verified.
- verify.py is the ONLY writer of last_verified, and only for records it actually checked this run.
- A carried page-checked row whose canonical URL disappears from the fresh fetch AND whose last_verified is older than the freshness window is EXCLUDED from the publishable inventory (archived), never silently refreshed.
- Per-method labels preserved (jobicy official status vs bounded content check vs manual page check) - Part 7 standard.

## Build reproducibility (critical)
pipeline/repro_build.py reproduces the CURRENT deployed production hashes byte-identically from template.html + data-release/<site>.json:
- bapm ddbeae8e49033b60, ai 694bc320cbd37dc0, marketing 6d6971b94a43b32b (verified 2026-11-22 02:21 EET).
The refresh MUST use this recipe (META_DESC from cfg['sub_en_rel'], data from data-release). Any first dry-run diff larger than the data delta = stop and investigate before any merge.

## Implemented since assembly
1. refresh.py orchestrator complete: collect -> classify -> verify -> merge (strict same-day carry, TTL 0) -> fail-closed publication gate -> rebuild via repro_build.build_rel(site, repo-root).
2. collect.py remotive block gated behind CAREER_ENABLE_REMOTIVE=1 (default off).
3. Site key passed as --site arg in the per-repo workflow (SITEKEY placeholder in refresh.yml).
4. Publication gate (fail-closed): collection non-empty; per-source non-empty (jobicy, arbeitnow); >=1 fresh current-run verification; publishable >= 50% of prev (prev>=10) and >= 5 absolute; zero recorded source errors. On breach: report written, release/index/state untouched, exit 3, commit step never runs.
5. Verification honesty: only explicit active jobicy status or a successful bounded content check sets verified_this_run (current-run marker); inherited same-day last_verified never counts as fresh; closed/unknown/empty statuses archive the row. State is staged to pipeline-state.json.next and promoted only after gate acceptance.
6. Template: UTC snapshot wording; method stats allowlist (status-endpoint / listing-checked / browser-reviewed only for browser_page_check / unknown-method otherwise).
