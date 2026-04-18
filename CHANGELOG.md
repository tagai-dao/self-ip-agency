# Changelog

All notable changes to Self-IP Agency will be documented in this file.

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
