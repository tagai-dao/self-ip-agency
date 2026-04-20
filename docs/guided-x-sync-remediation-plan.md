---
title: Guided X Sync Remediation Plan
status: ready-for-dispatch
updated: 2026-04-20
owner: Claude Dispatch
framework: gstack
recommended_skill: /ship
---

# Guided X Sync Remediation — Executable Plan

## Problem summary

The newly built self-IP Agent is failing X sync for **two independent but interacting reasons**:

1. **Guided X bootstrap was never truly initialized**
   - `config/agency-identity.json` has `owner.twitter_handle = null`
   - `runtime/shared/guided-x-urls.json` is missing
   - result: browser-guided path never starts; RSS/public fallback also fails because handle is missing

2. **Bookmarker feed parser still has contract drift**
   - live feed response shape is `{hasMore, page, success, tweets}`
   - current parser only recognizes `posts/items/data`
   - result: transport can succeed but still degrade into `schema mismatch`, `feed_size=0`, or false blocked state

This must be fixed as a coordinated remediation, not a one-line patch.

---

## Goals

1. Make X bootstrap truthfully usable on fresh self-IP deployments
2. Ensure missing `owner.twitter_handle` is surfaced as a clear blocker
3. Productize the **guided manifest producer**, not just the consumer
4. Accept `tweets` envelope in canonical bookmarker feed parsing
5. Separate bootstrap health from feed-parse health in runtime/dashboard semantics
6. Preserve zero-credential default path (no required X API keys; no manual cookie copying)

---

## P0 — Restore broken deployments to a truthfully runnable state

### P0-A. Enforce owner.twitter_handle at install/bootstrap time

#### Required behavior
- If `owner.twitter_handle` is missing:
  - do **not** attempt RSS/public fallback
  - do **not** emit vague generic `blocked`
  - emit explicit blocker: `missing_owner_twitter_handle`
- Installer / onboarding must either:
  - require manual X handle input, or
  - auto-backfill it from guided onboarding / verification flow when available

#### Files likely to change
- `scripts/install.sh`
- identity generation / detection scripts
- `scripts/sync_guided_x_tweets.py`
- possibly identity docs / templates

#### Acceptance criteria
- fresh install with missing handle reports `missing_owner_twitter_handle`
- no external discovery attempts are made when handle is missing
- next-steps output points operator to the exact fix

---

### P0-B. Productize guided manifest generation

Current repo can consume `runtime/shared/guided-x-urls.json`, but does not reliably create it.

#### Required behavior
Add a producer script such as:
- `scripts/discover_guided_x_urls.py`
- or `scripts/bootstrap_guided_x_session.py`

The producer must:
1. check browser/X session availability
2. perform browser-guided discovery for the target handle
3. search recent authored tweets and replies
4. collect discovered tweet URLs
5. write canonical manifest to `runtime/shared/guided-x-urls.json`

#### Canonical discovery intent
Use browser/chirp-style discovery for at least:
- `from:<handle> since:<date> until:<date>`
- `from:<handle> since:<date> until:<date> filter:replies`

#### Important
This is the missing half of the architecture. Do not leave the repo with consumer-only support.

#### Files likely to change
- new script under `scripts/`
- `scripts/install.sh`
- possibly docs / troubleshooting guidance

#### Acceptance criteria
- a deployment can generate `runtime/shared/guided-x-urls.json` without manual file authoring
- guided path becomes the real primary path, not just a documented ideal

---

### P0-C. Make RSS/public fallback degrade truthfully

#### Required behavior in `scripts/lib/x_fetch_utils.py`
- if handle is missing:
  - immediately return blocked with `missing_owner_twitter_handle`
- if RSS transport fails:
  - distinguish `rss_transport_failed`
- if RSS payload cannot be parsed:
  - distinguish `rss_parse_failed`
- if RSS parses but yields no items:
  - distinguish `rss_empty`

#### Acceptance criteria
Operator can tell whether the problem is:
- missing handle
- broken public endpoint
- parse failure
- valid empty result

---

## P0 — Fix parser contract drift

### P0-D. Accept `tweets` envelope in canonical feed parsing

#### Observed live shape

```json
{
  "hasMore": true,
  "page": 1,
  "success": true,
  "tweets": [...]
}
```

#### Required behavior
Canonical feed normalization must recognize, in order:
- `tweets`
- `posts`
- `items`
- `data`

#### Diagnostics to record
At minimum add / preserve:
- `feed_response_keys`
- `feed_response_type`
- `feed_parse_status`
- `feed_count_raw`
- `feed_count_parsed`
- `schema_mismatch_reason`

#### Semantic states to distinguish
1. transport ok + parse ok + parsed items > 0
2. transport ok + parse ok + parsed items == 0
3. transport ok + parse failed / schema mismatch
4. transport failed

#### Files likely to change
- `scripts/run_bookmarker_runtime_v1.py`
- any canonical feed adapter/helper used by bookmarker
- tests/fixtures for feed parsing

#### Acceptance criteria
- `{tweets: [...]}` no longer becomes schema mismatch
- real parsed feed items show up as parsed, not empty
- false blocked/empty conditions are eliminated for this shape

---

### P0-E. Separate bootstrap health from feed health

Current state conflates:
- guided bootstrap readiness
- bookmarker feed parsing health

#### Required state model
At minimum separate:

##### Bootstrap/X sync layer
- handle configured?
- guided manifest exists?
- browser guidance completed?

##### Feed/parser layer
- transport ok?
- parse ok?
- parsed count?
- valid empty vs invalid empty?

#### Recommended statuses
Use truth-oriented statuses such as:
- `ok`
- `valid_empty`
- `deferred`
- `blocked`
- `schema_mismatch`
- `transport_failed`

#### Files likely to change
- runtime summary artifacts
- `source-health.json`
- dashboard/server + frontend if needed
- X sync status reporting

#### Acceptance criteria
- guided bootstrap failure does not masquerade as feed parse failure
- feed parse mismatch does not masquerade as missing bootstrap
- operator can see exactly which layer is unhealthy

---

## P1 — Installer / operator experience hardening

### P1-A. Make install output actionable

#### Required behavior
`install.sh`, `.install-next-steps.json`, and install marker output must explicitly surface:
- `x_tweets_seed_status`
- `x_tweets_blockers`
- `guided_manifest_status`
- `owner_twitter_handle_status`

#### Expected UX
If handle missing:
- say that directly

If manifest not yet generated:
- say guided browser discovery is still required

Do not leave operator to infer this from downstream blocked states.

---

### P1-B. Update docs to match the real remediation path

#### Required docs updates
- guided X setup / troubleshooting docs
- install docs
- wiki/raw pipeline docs as needed

#### Must document
- default path remains zero-credential from the user perspective
- browser-guided manifest is the canonical producer path
- public/RSS is fallback only
- missing handle is a hard precondition blocker

---

## Validation plan

### Minimum required validation
1. **Missing handle case**
   - installer/sync reports `missing_owner_twitter_handle`
   - no RSS request attempted

2. **Manifest missing case**
   - guided status clearly reports missing manifest / deferred guidance
   - does not falsely report healthy sync

3. **`tweets` envelope case**
   - parser accepts `{tweets:[...]}`
   - parsed count > 0 when items exist

4. **Valid empty case**
   - reported as `valid_empty`
   - not green false-positive, not red hard failure

5. **Schema mismatch case**
   - reported as schema/parse issue with keys/reason diagnostics

6. **Guided producer case**
   - producer creates `runtime/shared/guided-x-urls.json`
   - consumer can ingest it into `raw/x-tweets/`

7. **Installer/reporting case**
   - next-steps and install marker truthfully reflect blocker type

### Nice-to-have
- regression fixtures for multiple envelope shapes (`tweets`, `posts`, `items`, `data`)
- dashboard semantic rendering verification if state labels changed

---

## Suggested implementation order

1. patch `owner.twitter_handle` enforcement and truthful blocker emission
2. patch RSS/public fallback status model
3. patch bookmarker parser to support `tweets` envelope
4. add/ship guided manifest producer
5. wire installer / next-steps / docs
6. add regression tests
7. validate on a fresh deployment path and summarize remaining caveats

---

## Guardrails

- Use gstack `/ship`
- Prefer durable fixes over one-off manual workarounds
- Do not regress existing guided X sync consumer pipeline already merged
- Do not revert zero-credential default path back to required X API / manual cookie copying
- Do not claim healthy sync unless bootstrap and parse states are both truthfully good
- Keep unrelated repo dirt out of commits

---

## Deliver back

Provide:
- root causes confirmed in code / runtime
- exact files changed
- exact contract/status changes
- validation performed
- PR link
- whether merged to main
- remaining caveats if any
