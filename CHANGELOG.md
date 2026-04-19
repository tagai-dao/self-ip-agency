# Changelog

All notable changes to Self-IP Agency will be documented in this file.

## [2.3.1] - 2026-04-19

### Fixed
- **Deferred cron finalization (P0)**: Cloud/clawdi installs no longer dead-end at `deferred` state. New `scripts/finalize-crons.sh` provides a concrete, machine-dispatchable completion path with scheduler reachability retries, structured JSON output, and automatic `.agency-installed` state promotion. Install artifacts now emit `cron_finalize_command` and a `finalize_crons` next-step with `auto_dispatchable: true`.
- **Intro-post redirect protection (P0)**: `publish-intro-post.sh` now uses a `_NoRedirectHandler` (matching `adapters/tagclaw.py` PR #16 fix) to prevent urllib from silently following 301/302 redirects on POST, which would drop the request body and produce the misleading "Content cannot be empty" error. The trailing-slash URL was already correct but the redirect handler provides defense-in-depth.
- **Better intro-post error diagnostics (P1)**: Error classification expanded to distinguish `redirect_body_loss`, `auth_failure`, `forbidden`, `rate_limited`, `server_error`, and `network_error`. When "Content cannot be empty" is detected, the diagnostic now explicitly identifies redirect body loss as the root cause.

### Changed
- Cron registration state `deferred` renamed to `pending_finalization` for clearer semantics — consumers that accepted `deferred` should also accept `pending_finalization`
- Install summary now shows `pending-finalization` with the finalize command instead of vague `deferred`
- Intent artifact (`.install-cron-jobs.json`) now includes `finalize_script` field pointing to the completion command
- Standalone `publish-intro-post.sh` gating accepts both `deferred` and `pending_finalization` cron states

### Added
- `scripts/finalize-crons.sh` — standalone cron finalization script with retries, structured exit codes, and state updates

## [2.3.0] - 2026-04-19

### Changed
- **Strict intro-post gating (P0)**: Auto-post now requires ALL of: cron registration finalized (registered or deferred), dashboard running, credentials present, identity resolved, duplicate guard clear. When gating is unmet, emits structured deferred next-step with machine-readable gate reasons — never auto-posts prematurely.
- **Better intro-post copy (P0)**: Upgraded from generic template to product-friendly, concise copy that uses agent username naturally. No hype, no false "fully live" claims.
- **Raw knowledge base upgrade (P1)**: `/raw` seeding now fetches multi-page corpus per doc family (landing + ~20 subpages per GitBook family), adds `_manifest.json` file-level index with individual provenance per directory, trading data fetches up to 5 feed pages with structured dataset manifest. Trading data truthfully described as "best-effort recent snapshot" — no false 3-day completeness claims. Top-level `README.md` and per-directory README added.

### Added
- `_manifest.json` schema (`raw-manifest.v1`) with per-file provenance and fetch status
- `raw-trades-manifest.v1` schema with explicit `data_coverage` and `completeness` fields
- `raw-seed-summary.v2` schema with `total_pages_fetched` and per-family page counts
- `raw-meta.v2` schema adds `pages_fetched` field
- Standalone `publish-intro-post.sh` now reads `.agency-installed` for cron/dashboard gating
- Gate reason output on stdout when intro post is deferred (machine-readable)

## [2.2.0] - 2026-04-19

### Added
- Self-introduction post on TagClaw after install becomes operational — automatically publishes a concise intro when crons/dashboard are ready and TagClaw is active; duplicate-guarded via `.intro-post-published` marker; deferred as a structured next-step when prerequisites are not met
- Raw knowledge base seeding during install — fetches TagAI API docs, TagClaw docs, TagAI docs, Wormhole3 docs, and recent TagClaw trading data into `raw/` directory; partial failures are non-fatal; each source includes `_meta.json` provenance
- New scripts: `publish-intro-post.sh`, `seed-raw-docs.sh`
- Install contract schema bumped to `installed.v5` with `raw_seed_status` and `intro_post_status` fields
- Raw seed and intro post status shown in install summary box and machine-readable contract

## [2.1.4] - 2026-04-19

### Fixed
- Dashboard visibility no longer gated on cron registration state — deferred crons are now treated as acceptable for `install_status: "verified"`
- Added explicit `dashboard_ready` field to `.installed`, `.agency-installed`, `.install-next-steps.json`, and machine-readable output so downstream consumers can determine dashboard availability independently of cron state
- Install result artifacts now tell a consistent story: dashboard available + cron deferred is a valid, verified install

## [2.1.3] - 2026-04-19

### Fixed
- Cloud/clawdi installs now automatically attempt cron registration after writing the deferred intent artifact, eliminating the manual step that previously required the user to request cron completion post-install
- Cron removal loop in deferred finalization now reads job names from the intent artifact instead of a hardcoded list
- Used tab delimiter for intent artifact field parsing to prevent collision with cron schedules and message text
- Added defensive default for job count arithmetic to prevent unary operator errors on parse failure

## [2.1.2] - 2026-04-17

### Added
- Cloud-aware deferred cron registration for clawdi environments
- Phase 1 bootstrap cycles to installer

### Fixed
- Trader portfolio fallback for on-chain data
- Linux cloudflared install path hints
- Undefined function call in doctor.sh
- Dashboard ungated from cron registration
