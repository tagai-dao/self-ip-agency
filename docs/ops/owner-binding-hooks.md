---
title: Owner X/Twitter binding — ops hooks
status: stable
updated: 2026-04-21
scope: openclaw ops / cloud deployers
---

# Owner X/Twitter binding hooks

This document describes the files, events, and state the self-ip-agency
installer + heartbeat publish so that the openclaw ops layer (or any external
automation) can observe and orchestrate owner X binding resolution **without
modifying this repo**. All paths live under `<workspace>` and all artifacts
are newline-terminated, atomically-replaced JSON.

The repo does **not** ship systemd units, launchd plists, cron files, or any
process-supervisor config for this feature (deliberate: see §4.3.1 of
`docs/design/x-sync-twitter-binding-fix.md` + user decision §7 #5). Ops owns
that lifecycle. The contract below is what the repo promises; how you wire
it is your call.

---

## 1. Triggers you can listen for

### 1.1 Event file — `runtime/shared/events/owner-binding-resolved.json`

Written **exactly once per verification**: when `main-heartbeat.sh`'s
self-heal phase receives a `/me` response that contains a non-empty
`ownerTwitterHandle` AND `refresh-agency-identity.sh --verify-api` flips
`agency-identity.json:owner.verified` to `true`. Overwritten on subsequent
confirmations (idempotent rewrite), not appended.

**Schema** (`owner-binding-resolved.v1`):

```json
{
  "schema": "owner-binding-resolved.v1",
  "resolved_at": "2026-04-21T18:30:12Z",
  "twitter_handle": "thefandotfun",
  "twitter_id": "1863098517117702145",
  "source": "tagclaw.me.verified"
}
```

**Semantics**:

- File exists ⇔ at least one successful verification has occurred during
  this workspace's lifetime.
- `source` is always `tagclaw.me.verified` in v1 (only /me can produce a
  verified binding); future versions may add other values.
- Consumers can `stat(2)` + compare `resolved_at` to detect fresh events,
  or use `fs.inotify` / `fswatch` for push-style hooks.

### 1.2 State file — `runtime/shared/identity-sync.json`

The heartbeat self-heal ledger. Updated **on every attempt** (pre-run stamp
+ post-run result). Read this to answer "is self-heal still in flight?" vs
"has it converged?".

**Schema** (`identity.sync.v1`):

```json
{
  "schema": "identity.sync.v1",
  "last_attempt_at": "2026-04-21T18:30:00Z",
  "last_success_at": "2026-04-21T18:30:12Z",
  "attempts": 3,
  "verified": true,
  "last_error": null,
  "source": "tagclaw.me.verified",
  "disabled": false
}
```

**Semantics**:

- `verified: true` means heartbeat considers the binding resolved and will
  **skip /me** on subsequent runs until something flips it back to `false`
  (not a normal code path).
- `verified: false` with `attempts > 0` means self-heal is actively
  retrying; consumers (e.g. `sync_guided_x_tweets.py`, your alerting)
  should treat sync-blocked states as "deferred, not failed".
- `disabled: true` (set by ops, not by the repo) halts self-heal. Use this
  to stop /me traffic when the binding is known to be permanently
  unavailable or when TagClaw rate limits become a concern.
- `last_error` is a short free-form string with `rc=<n>` + optional log
  tail; not machine-parseable. Use `attempts - last_success_attempt` for
  health signals, not this field.

### 1.3 Dashboard surface — `GET /api/status`

When the dashboard is running (`scripts/dashboard-service.sh start-local`),
the `/api/status` payload includes:

```json
{
  "owner_binding": {
    "status": "verified",
    "twitter_handle": "thefandotfun",
    "twitter_id": "1863098517117702145",
    "source": "tagclaw.me.verified",
    "verified": true,
    "last_verified_at": "2026-04-21T18:30:12Z",
    "self_heal": { ... identity-sync.json verbatim ... }
  }
}
```

`status` maps: `verified` | `declared` | `unresolved` | `unknown`. See
design doc §4.5.

### 1.4 Install contract — `.install-next-steps.json`

Immediately after `scripts/install.sh` finishes, the top-level JSON contains
a new `owner_binding` block:

```json
{
  "owner_binding": {
    "status": "unresolved",
    "reason": "awaiting_tagclaw_me_or_post_verify",
    "self_heal": "heartbeat",
    "declared_source": "none"
  }
}
```

`status` values: `verified` | `declared` | `unresolved` | `unknown`.
`reason` values: `verified_via_me` | `declared_pending_verify` |
`awaiting_tagclaw_me_or_post_verify` | `identity_not_written` |
`identity_parse_error` | `empty`. `declared_source` echoes the origin of
any operator declaration: `flag` | `env` | `file` | `tty` | `none`.

Install **never fails** on unresolved binding (design §4.5 + user
decision §7 #4); consumers should treat `unresolved` on a fresh install
as normal and expect heartbeat to flip it to `verified`.

---

## 2. Inputs ops can set

### 2.1 Non-interactive handle declaration

Priority: `--owner-twitter-handle` flag > `OWNER_TWITTER_HANDLE` env var >
`config/owner.local.json` (gitignored). See `scripts/install.sh:collect_owner_twitter_binding`.

`config/owner.local.json` schema:

```json
{
  "owner": {
    "twitter_handle": "thefandotfun",
    "twitter_id": "1863098517117702145"
  }
}
```

Any supplied value is written to `<workspace>/skills/tagclaw/.env` as
`TAGCLAW_EXPECTED_TWITTER_HANDLE` / `TAGCLAW_EXPECTED_TWITTER_ID`. These are
**operator-declared**, not verified; they populate `agency-identity.json`
with `binding_source=operator.declared`, `verified=false`, and heartbeat
self-heal keeps probing /me until it confirms.

### 2.2 Disabling self-heal

Write `{"schema":"identity.sync.v1","disabled":true}` to
`<workspace>/runtime/shared/identity-sync.json`. Heartbeat will skip the
self-heal phase on every subsequent run. Useful when:

- TagClaw /me is known not to expose `ownerTwitterHandle` for this account.
- Rate budget concerns (user decision §7 #4 allows per-heartbeat /me calls,
  but ops can override).
- Debugging: you want to hand-edit identity JSON without heartbeat racing.

### 2.3 Force-triggering a single self-heal run

Invoke the refresh helper directly:

```bash
bash <workspace>/scripts/refresh-agency-identity.sh \
  --workspace <workspace> --verify-api
```

Exits 0 on success (identity JSON written + verified=true), exit 2 on
"active but /me has not returned ownerTwitterHandle" (heartbeat will keep
retrying). No other exit codes currently fire for this feature.

---

## 3. What ops should NOT do

- **Do not** touch `<workspace>/config/agency-identity.json` directly —
  hand-edits race with `refresh-agency-identity.sh`'s atomic write. If you
  need to patch identity, set the source in skill `.env` and let the
  refresh helper regenerate.
- **Do not** write `identity-sync.json.verified=true` to suppress
  heartbeat; heartbeat trusts this value and will skip /me forever. Use
  `disabled: true` instead if you want to halt self-heal.
- **Do not** grep `HEARTBEAT_STATUS` for owner-binding errors. Self-heal
  failures are **isolated** by design (§4.4): `/me` network / auth errors
  stay in `identity-sync.json.last_error` and never propagate into
  `HEARTBEAT_STATUS`. If you're relying on heartbeat exit code to detect
  binding breakage, change the hook to watch `owner_binding.status` or
  `identity-sync.json.verified` instead.
- **Do not** confuse this with scheduler reachability (PR #5/#6). The
  `/me` probe uses `curl` directly and has no interaction with the
  openclaw cron scheduler probes; a `/me` outage is **not** a
  `scheduler_unreachable` event.

---

## 4. Example ops integrations

### 4.1 Dispatch a post-resolution webhook (pseudo-bash)

```bash
WATCH="<workspace>/runtime/shared/events/owner-binding-resolved.json"
fswatch -1 "$WATCH" | while read -r _; do
  curl -X POST https://your-ops-webhook.example/resolved \
    -H 'Content-Type: application/json' \
    --data-binary "@$WATCH"
done
```

### 4.2 Alert when self-heal has been stuck for > N heartbeats

```python
import json, pathlib, time
state = json.loads(pathlib.Path("<workspace>/runtime/shared/identity-sync.json").read_text())
if not state.get("verified") and int(state.get("attempts", 0)) >= 12:
    alert(f"owner binding stuck: attempts={state['attempts']}, last_error={state.get('last_error')}")
```

(At a 5-minute heartbeat cadence, 12 attempts = 1 hour without /me
resolution. Design §4.4 suggests this threshold is the natural escalate
point.)

### 4.3 One-shot finalize from a systemd oneshot / launchd plist

Ops can optionally trigger the full post-verify flow once at boot instead
of waiting for the next heartbeat. Create a unit that runs:

```bash
bash <workspace>/scripts/tagclaw-onboard.sh post-verify-finalize \
  --workspace <workspace>
```

`post-verify-finalize` is idempotent; running it when the account is
already active and verified is a no-op.

---

## 5. Backwards compatibility

- Pre-PR-B workspaces (no `identity-sync.json`, no `binding_source`) read
  fine: heartbeat starts fresh state on first run; dashboard returns
  `status=unresolved` (handle null) or `status=declared` (handle present
  but no `verified` key ⇒ treated as false).
- Pre-PR-B consumers of `agency-identity.json` (`sync_guided_x_tweets.py`,
  `verify_wiki_contract.py`, `run_bookmarker_runtime_v1.py`,
  `dashboard/server.py`) only `.get()` the canonical two fields
  (`twitter_handle`, `twitter_id`). New optional keys (`binding_source`,
  `verified`, `last_verified_at`) are additive — no schema break.
- Install contract bumps to `install-next-steps.v2` (unchanged top-level
  schema; the `owner_binding` block is additive).

---

## 6. References

- Design proposal: `docs/design/x-sync-twitter-binding-fix.md`
- PR-A (hotfix): #27, commit `48b2899`
- PR-B (self-heal + UX): this PR
