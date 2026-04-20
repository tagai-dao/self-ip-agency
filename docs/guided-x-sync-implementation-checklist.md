---
title: Guided X Sync Implementation Checklist
status: ready-for-build
updated: 2026-04-20
owner: Claude Dispatch
framework: gstack
recommended_skill: /ship
---

# Guided X Sync — Implementation Checklist

## Goal

After a user installs self-IP Agency with:

```bash
git clone https://github.com/tagai-dao/self-ip-agency ~/self-ip-agency
cd ~/self-ip-agency
bash scripts/install.sh
```

…the installer should be able to bootstrap the owner's recent X data into `raw/x-tweets/` **without requiring any X API keys or manual cookie/token setup**.

The only user-facing requirement should be:
- provide / confirm the owner's X handle
- complete one guided X login / account-guidance step in browser if needed

Then the system should:
1. discover the owner's tweets + replies from the past 3 days
2. write normalized raw artifacts into `raw/x-tweets/`
3. compile them into wiki synthesis artifacts
4. surface truthful status in installer outputs / next steps / verification

---

## Canonical data-source strategy

### Default / required path
Use **browser-guided X sync**:
- use OpenClaw browser session (or chirp-style browser flow) to discover tweet URLs
- use a zero-config fetcher pattern similar to `x-tweet-fetcher` for per-tweet structured retrieval
- do **not** require X API keys
- do **not** require users to manually copy cookies / auth tokens

### Optional advanced path
May support direct X API or bird-based cookie auth later, but:
- they are **not** the default path
- they must **not** be required for successful install bootstrap

### Explicit rejection
Do **not** make `bird` + `AUTH_TOKEN` / `CT0` the primary install path.

---

## Product constraints

- Install must remain truthful and robust.
- X bootstrap must be **non-fatal** if guidance/login is incomplete.
- `raw/` remains immutable source storage.
- Wiki compilation should consume `raw/x-tweets/` rather than talking to X directly.
- No secrets committed.
- No fake green status when the sync never actually ran.

---

## Deliverables

## D1. Bootstrap sync script
Create a script to bootstrap guided X data into raw.

### Proposed file
- `scripts/sync_guided_x_tweets.py`

### Responsibilities
- read `config/agency-identity.json`
- resolve `owner.twitter_handle` (and `twitter_id` if available)
- verify whether an X browser session is available / guided
- discover tweet URLs for the past 3 days:
  - authored tweets
  - replies
- fetch structured content for each discovered URL
- normalize into `raw/x-tweets/`
- write run summary / manifest / meta
- exit with truthful status codes / JSON output

### Required CLI shape
At minimum support:

```bash
python3 scripts/sync_guided_x_tweets.py \
  --workspace <path> \
  --handle <optional-override> \
  --lookback-days 3 \
  --include-replies \
  --json
```

### Required output contract
Structured JSON summary, e.g.:

```json
{
  "status": "ok|partial|blocked|deferred|error",
  "handle": "0xNought",
  "lookback_days": 3,
  "discovery_method": "browser-guided",
  "tweet_urls_found": 12,
  "items_written": 10,
  "items_skipped_existing": 2,
  "raw_dir": "raw/x-tweets",
  "blockers": []
}
```

---

## D2. Raw storage contract
Create / honor the following structure:

```text
raw/x-tweets/
  _meta.json
  _manifest.json
  sync-runs/
    <timestamp>.json
  tweets/
    <tweet_id>.json
```

### Raw file schema (minimum)
Each tweet file should include at least:
- `schema`
- `tweet_id`
- `author_handle`
- `created_at`
- `fetched_at`
- `kind`
- `is_reply`
- `conversation_id` (if known)
- `in_reply_to_tweet_id` (if known)
- `text`
- `url`
- `source.provider`
- `source.discovery`
- `source.content_fetch`
- `source.query_window_days`
- engagement fields when available

### Dedup rules
- primary key = `tweet_id`
- never overwrite an existing raw tweet silently
- record new / skipped / failed counts in manifest + sync-run summary

---

## D3. Wiki compile step
Create the compiler from `raw/x-tweets/` to wiki synthesis.

### Proposed file
- `scripts/build_x_tweets_wiki_v1.py`

### Responsibilities
- read `raw/x-tweets/tweets/*.json`
- classify as owned tweets / replies
- extract themes and map concepts via `scripts/wiki_registry.py`
- write synthesis pages to `wiki/synthesis/tweets/`
- append truthful log entries to `wiki/log.md` if that is the existing pattern

### Expected output
For each compiled item, produce markdown with frontmatter including at least:
- `tweet_id`
- `author`
- `created_at`
- `source_file`
- `concepts`
- `updated`

---

## D4. Installer integration
Patch `scripts/install.sh` so guided X bootstrap happens at the correct point.

### Required behavior
Keep `seed_raw_docs()` separate.
Do **not** stuff this into `seed-raw-docs.sh`.

### Recommended install flow
After identity + onboarding / browser guidance is sufficiently available:

1. `seed_raw_docs`
2. `bootstrap_guided_x_session` (if needed)
3. `sync_guided_x_tweets`
4. `build_x_tweets_wiki_v1`

### Truthful install semantics
If X guidance/login is incomplete:
- do not fail the whole install
- mark X bootstrap as `deferred` / `blocked`
- emit next-step guidance in `.install-next-steps.json`

### Status fields to add/update
Add truthful install result fields such as:
- `x_tweets_seed_status`
- `x_tweets_compiled_count`
- `x_tweets_window_days`
- `x_tweets_blockers`

---

## D5. Browser-guidance UX / contract
Implement the minimum browser-guided contract required for zero-credential onboarding.

### Requirements
- detect whether X session is already available
- if not, prompt/guide the operator to log in once
- once login succeeds, continue without requiring manual cookie extraction

### Important
The canonical user ask is:
- “guide your X account once”
not
- “go open devtools and paste auth tokens”

---

## D6. Verification / contract updates
Patch verification so this path becomes part of the durable system.

### Files likely involved
- `scripts/verify_wiki_contract.py`
- possibly docs / contract references

### Add checks for
- `raw/x-tweets/` existence
- `_meta.json` + `_manifest.json`
- sync summary truthfulness
- if raw tweets exist, compiled wiki tweets should also exist
- no false ok/green when data never synced

---

## D7. Documentation updates
Update docs so future operators understand the new canonical path.

### Expected docs to update
- `README.md`
- `docs/x-setup.md`
- `docs/wiki-guide.md`
- `schema/ingest-rules.md`
- any install / troubleshooting docs that mention X setup

### Doc changes required
Explain that the default path is:
- guided X handle + browser session
- not X API keys
- not manual cookie extraction

---

## D8. Tests / validation
Add the minimum test coverage for the new path.

### Required validation
1. clean install path with no X API creds does not hard-fail
2. missing X login yields `deferred` or `blocked`, not fake success
3. successful sync writes:
   - raw meta
   - raw manifest
   - tweet raw files
4. wiki compiler writes synthesis pages from raw files
5. verifier reports the correct status

### Nice-to-have
- normalization unit tests for raw schema
- dedup tests for repeated bootstrap runs
- reply classification test

---

## Phase plan

## Phase 1 — contract + bootstrap skeleton
- add raw schema + directory contract
- add `sync_guided_x_tweets.py` skeleton
- add installer status plumbing
- ensure missing login produces truthful `deferred/blocked`

## Phase 2 — discovery + fetch
- implement browser-guided discovery of tweet URLs
- integrate structured fetch for discovered URLs
- write normalized raw files + manifests

## Phase 3 — raw → wiki
- implement `build_x_tweets_wiki_v1.py`
- connect installer and verifier
- update docs

## Phase 4 — hardening
- tests
- idempotency / dedup
- better operator guidance / next steps

---

## Files likely to change

### New
- `scripts/sync_guided_x_tweets.py`
- `scripts/build_x_tweets_wiki_v1.py`
- optional schema file for raw tweet contract
- optional docs for guided sync behavior

### Modified
- `scripts/install.sh`
- `scripts/verify_wiki_contract.py`
- `README.md`
- `docs/x-setup.md`
- `docs/wiki-guide.md`
- `schema/ingest-rules.md`
- possibly `adapters/tagclaw.py` or helper modules if a reusable fetch/discovery wrapper is created

---

## Acceptance criteria

This task is complete only if all are true:

1. a fresh install no longer assumes X API credentials
2. the user only needs to provide / guide their X account via browser login
3. recent tweets + replies can be written to `raw/x-tweets/` through the guided path
4. raw artifacts are immutable + deduplicated
5. wiki synthesis is built from those raw artifacts
6. install / verifier statuses are truthful when sync is unavailable
7. docs reflect the new default path

---

## Guardrails for Claude Dispatch

- Use gstack `/ship` as primary workflow.
- Prefer the durable path over one-off hacks.
- Do not add a brittle dependency on copied browser cookies.
- Do not claim success unless raw files were actually written.
- Do not silently downgrade missing login into green ok status.
- Avoid unrelated repo cleanup unless required for this task.
- Ignore / do not commit unrelated dirty files such as local caches, `__pycache__`, or ad-hoc `skills/` contents.

---

## Suggested implementation order for the agent

1. inspect current install / wiki / verifier contracts
2. implement raw contract + sync skeleton
3. patch installer status plumbing
4. implement guided discovery + structured fetch path
5. implement raw→wiki compiler
6. add tests / verification
7. update docs
8. run validation and summarize changed files + remaining caveats
