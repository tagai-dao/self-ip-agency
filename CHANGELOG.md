# Changelog

All notable changes to Self-IP Agency will be documented in this file.

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
