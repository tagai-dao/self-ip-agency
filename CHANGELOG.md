# Changelog

All notable changes to Self-IP Agency will be documented in this file.

## [2.5.1] - 2026-04-21

### Fixed
- **Cron registration reachability probe**: `openclaw cron list` was the sole probe for scheduler reachability across `install.sh`, `finalize-crons.sh`, and deferred finalization. A healthy scheduler with zero registered jobs could return non-zero from `cron list`, causing the installer to misclassify it as `scheduler_unreachable`. Now uses a multi-signal probe (`cron list` + `health --json` + `cron status` fallbacks) via a shared `probe_scheduler_reachable` function in `lib/common.sh`.
- **Inconsistent probe logic**: `_detect_cron_registration_mode`, `_attempt_deferred_cron_finalization`, and `finalize-crons.sh` all lacked the `health --json` fallback that `register_crons` had. All four sites now use the same shared probe.
- **Opaque error reporting**: Scheduler probe failures now surface the specific `_PROBE_RESULT` (`unreachable` vs `cli_broken`) in log messages and JSON output, making diagnosis easier for operators.

## [2.5.0] - 2026-04-19

### Added
- **Intro-post tick resolver (`scripts/resolve-intro-post-tick.py`)**: Deterministic priority chain for selecting the intro-post tick/community instead of hardcoding `IPShare`. Resolution order: (1) explicit override via `--tick` flag or `INTRO_TICK` env, (2) local `/raw` knowledge base inference from trending data, feed pages, and tagclaw-posts, (3) validated fallback to `IPShare` only when no local data is available.
- **Tick resolution fields in install state**: New `intro_post_tick`, `intro_post_tick_status`, `intro_post_tick_source` fields in installed state contract (schema bumped to `installed.v6`) and `.install-next-steps.json`. Machine-readable output includes `INTRO_POST_TICK`, `INTRO_POST_TICK_STATUS`, `INTRO_POST_TICK_SOURCE`.
- **`--tick` flag on `publish-intro-post.sh`**: Explicit tick override for standalone invocation.

### Changed
- **Intro-post no longer hardcodes `IPShare`**: `publish-intro-post.sh` now calls the tick resolver before publishing. If tick cannot be resolved, the post is truthfully deferred instead of guessing.
- **Install summary box** now shows tick resolution details (tick name, source, status) alongside intro-post status.
- **Marker file** (`.intro-post-published`) now includes `tick_source` and `tick_status` for provenance.
- Install state schema bumped from `installed.v5` to `installed.v6`.

## [2.4.0] - 2026-04-19

### Fixed
- **Intro-post API contract mismatch (P0)**: The real TagClaw API uses `text` (not `content`) and requires a mandatory `tick` field. Fixed `adapters/tagclaw.py` and `publish-intro-post.sh` to use the canonical `{"text": ..., "tick": ...}` payload. Default tick is `IPShare`.
- **Reply API contract (P0)**: `adapters/tagclaw.py` `reply()` also fixed from `content` to `text`.
- **Marker write crash (P0)**: Replaced fragile nested `$(python3 ...)` subshell marker writer with a single robust Python invocation using env vars. If the post succeeds but marker write fails, the script now exits 0 with `outcome=published_but_marker_failed` instead of crashing — no false failure, no duplicate-post risk from re-runs.
- **Deferred cron finalization UX (P0)**: `finalize-crons.sh` now sets `mode: finalization_dispatched` before attempting registration, giving operators clear state visibility. Install summary box now shows the finalize command inline. Machine-readable output includes `CRON_FINALIZE_COMMAND`.

### Changed
- **Truthful intro-post status model**: New `published_but_marker_failed` status distinguishes "post is live but marker write failed" from generic failures. Install summary box renders this distinctly.
- **Better intro-post diagnostics**: Error classification now includes `api_contract_mismatch`, `invalid_tick`, `redirect_error`, `transport_error` categories with structured `diagnostic` field in JSON output.
- Intent artifact mode renamed from `deferred-tool-registration` to `pending_finalization` for clearer state machine semantics
- Cron state machine: `pending_finalization` → `finalization_dispatched` → `finalized` (or stays `pending_finalization` on failure)

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
