# Self-IP LLM Wiki v2 Implementation Spec

> Status: implementation-ready
> Updated: 2026-04-13
> Depends on: `docs/self-ip-llm-wiki-v2-design.md`, `docs/wiki-runtime-contract-v1.md`
> Goal: turn the v2 design into a phased, engineer-executable implementation plan for Claude Dispatch / Codex / other coding agents.

---

## 1. Objective

Implement the next version of the existing self-IP LLM Wiki **without discarding the current local-first architecture**.

This spec is intentionally practical:

- exact directories to create
- exact artifacts to add
- exact scripts to implement
- exact contracts to preserve
- phased rollout order
- acceptance criteria per phase
- non-goals / guardrails

The outcome should be:

1. explicit **schema / resolver** layer,
2. explicit **claim / thesis** layer,
3. explicit **feedback compilation** layer,
4. explicit **identity-safe adaptation boundary**,
5. continued compatibility with the existing **raw/wiki/runtime/contract** architecture.

---

## 2. Existing constraints that MUST be preserved

## 2.1 Source-of-truth constraints

These are already correct and must remain true:

- `raw/**` is immutable source input
- `wiki/identity/**` remains protected and manual-governed
- `runtime/**` remains derived, never canonical
- `docs/wiki-runtime-contract-v1.md` remains the v1 contract baseline unless explicitly superseded by a v2 contract doc

## 2.2 Current directories to preserve

Do not remove or rename these without explicit migration handling:

- `raw/x-tweets/`
- `raw/x-bookmarks/`
- `raw/x-likes/`
- `raw/x-interactions/`
- `raw/tagclaw-posts/`
- `raw/onchain-token-transation/`
- `raw/external-docs/`
- `wiki/concepts/`
- `wiki/synthesis/`
- `wiki/queries/`
- `wiki/execution/`
- `wiki/lint/`
- `wiki/tagclaw-platform/`
- `wiki/identity/`
- `wiki/INDEX.md`
- `wiki/log.md`

## 2.3 Protected files / directories

The following are read-only from the perspective of automatic wiki maintenance unless a human explicitly approves:

- `wiki/identity/persona.md`
- `wiki/identity/key-positions.md`
- future identity-anchor files under `wiki/identity/`

No automated phase should directly rewrite them.

---

## 3. Target deliverables

v2 implementation should produce these new durable assets.

## 3.1 New top-level directories

```text
schema/
skills/
```

## 3.2 New wiki directories

```text
wiki/claims/
wiki/theses/
```

Optional later if needed:

```text
wiki/entities/
```

## 3.3 New raw directory

```text
raw/feedback/
```

## 3.4 New runtime artifacts

```text
runtime/shared/claim-index.json
runtime/shared/thesis-index.json
runtime/shared/feedback-summary.json
runtime/shared/identity-strategy.json
runtime/shared/resolver-pack.json
runtime/shared/identity-drift-report.json
```

## 3.5 New docs

```text
docs/self-ip-llm-wiki-v2-design.md
docs/self-ip-llm-wiki-v2-implementation-spec.md
docs/self-ip-resolver-contract-v1.md
```

---

## 4. Phase plan

Implementation should be done in **five phases**.
Each phase should be independently shippable and verified.
Do not collapse all phases into a single giant unreviewable diff.

---

## Phase 1 — Schema extraction + resolver foundation

### Purpose

Make the currently scattered operational rules explicit and loadable.

### Deliverables

Create:

```text
schema/README.md
schema/ingest-rules.md
schema/query-rules.md
schema/lint-rules.md
schema/feedback-rules.md
schema/thesis-rules.md
schema/publish-rules.md
schema/identity-safety.md
schema/artifact-routing.md
schema/resolver-map.yaml
skills/README.md
```

### Required content

#### `schema/identity-safety.md`
Must define:

- what may auto-update
- what may not auto-update
- what requires owner approval
- how feedback is allowed to influence strategy but not core identity

#### `schema/resolver-map.yaml`
Must define task families at minimum:

- `ingest-source`
- `answer-query`
- `draft-content`
- `lint-wiki`
- `update-thesis`
- `process-feedback`
- `prepare-briefing`

Each entry should specify:

- files/directories to load
- optional runtime artifacts to load
- protected paths not to mutate

### Required scripts

Create:

```text
scripts/build_resolver_pack.py
```

Behavior:

- reads `schema/resolver-map.yaml`
- validates referenced paths exist (or records missing)
- emits `runtime/shared/resolver-pack.json`

### Acceptance criteria

- `schema/` exists and is populated
- `resolver-map.yaml` is valid and machine-readable
- `scripts/build_resolver_pack.py` runs successfully
- `runtime/shared/resolver-pack.json` is emitted
- no existing v1 behavior is broken

### Non-goals

- no claim/thesis extraction yet
- no identity drift automation yet

---

## Phase 2 — Claim + thesis layer

### Purpose

Upgrade the wiki from topic summaries into explicit worldview tracking.

### Deliverables

Create:

```text
wiki/claims/README.md
wiki/theses/README.md
```

Add at least one sample schema/example page in each directory.

### Required scripts

Create:

```text
scripts/build_claim_index.py
scripts/build_thesis_index.py
```

### Input assumptions

- claims and theses are stored as markdown with YAML frontmatter
- indexers compile frontmatter + selected derived fields into JSON indexes

### Claim schema (minimum)

Required frontmatter fields:

```yaml
id:
statement:
status:
confidence:
first_seen:
last_reinforced:
domain:
linked_theses:
```

Optional fields:

```yaml
supports:
contradicted_by:
notes:
```

### Thesis schema (minimum)

Required frontmatter fields:

```yaml
id:
title:
status:
confidence:
scope:
first_compiled_at:
last_revised_at:
revision_state:
```

Optional fields:

```yaml
supporting_claims:
counterarguments:
content_angles:
notes:
```

### Outputs

Emit:

- `runtime/shared/claim-index.json`
- `runtime/shared/thesis-index.json`

### Acceptance criteria

- both directories exist
- both indexers run successfully
- both runtime artifacts are emitted
- malformed pages are reported clearly, not silently skipped without trace

### Non-goals

- do not auto-generate hundreds of claims yet
- start with index infrastructure and a small seed set

---

## Phase 3 — Feedback compilation layer

### Purpose

Make feedback a first-class input to strategy, while protecting identity core.

### Deliverables

Create:

```text
raw/feedback/
```

Create scripts:

```text
scripts/build_feedback_summary.py
scripts/build_identity_strategy.py
```

### Feedback summary model

`feedback-summary.json` should aggregate:

- topic resonance
- format wins / losses
- source-type resonance
- platform-specific signal
- recent recognized items

### Identity strategy model

`identity-strategy.json` should contain only adaptive fields, for example:

```json
{
  "topic_weights": {},
  "format_preferences": {},
  "platform_heuristics": {},
  "source_priority": {},
  "derived_from": []
}
```

It must **not** contain or mutate:

- persona core text
- immutable worldview statements
- protected identity wording

### Acceptance criteria

- `raw/feedback/` exists
- scripts run successfully on empty and non-empty input
- `runtime/shared/feedback-summary.json` emitted
- `runtime/shared/identity-strategy.json` emitted
- adaptation allowlist / denylist is documented in `schema/identity-safety.md`

### Non-goals

- no automatic rewrite of `wiki/identity/*`
- no engagement-maxxing behavior outside documented adaptive fields

---

## Phase 4 — Resolver-driven workflow integration

### Purpose

Actually use the resolver layer in operational flows.

### Required integration points

At minimum, make resolver packs consumable by:

- ingest flow
- query flow
- drafting flow
- lint flow

This does **not** require rewriting the entire system. It can be introduced as a compatibility layer.

### Required script(s)

Possible options:

```text
scripts/load_resolver_context.py
```

or equivalent helper module.

### Behavior

Given a task name, the helper should:

- load task definition from `runtime/shared/resolver-pack.json`
- validate referenced paths
- return a structured list of files / directories / artifacts to load
- expose protected paths for write guards

### Acceptance criteria

- at least one existing wiki operation uses resolver-pack instead of ad-hoc broad context
- operation output shows which resolver entry was used
- protected-path guard remains intact

### Non-goals

- do not attempt universal retrofitting in one shot
- resolver integration can start in only one or two high-value flows first

---

## Phase 5 — Identity drift governance

### Purpose

Detect system drift before it becomes identity drift.

### Deliverables

Create:

```text
scripts/check_identity_drift.py
runtime/shared/identity-drift-report.json
```

### Input sources

Should compare, at minimum:

- recent durable outputs (`wiki/queries/`, selected synthesis pages, draft outputs if available)
- `wiki/identity/persona.md`
- `wiki/identity/key-positions.md`
- `wiki/theses/`
- `runtime/shared/identity-strategy.json`

### Output model

Should classify findings into:

- `none`
- `watch`
- `warning`
- `critical`

And include:

- suspected drift category
- supporting file references
- recommended action
- whether human review is required

### Acceptance criteria

- script runs successfully
- report emitted even when no drift is found
- no automatic edits to identity anchors

### Non-goals

- no self-rewriting of persona
- this is detection/reporting first, not autonomous correction

---

## 5. File-level implementation details

## 5.1 `schema/resolver-map.yaml`

Minimum shape:

```yaml
version: self-ip-resolver-v1
protected_paths:
  - wiki/identity/persona.md
  - wiki/identity/key-positions.md

tasks:
  ingest-source:
    load:
      - schema/ingest-rules.md
      - wiki/INDEX.md
      - wiki/log.md
      - wiki/concepts/
      - wiki/theses/
      - runtime/shared/claim-index.json
    protected_writes:
      - wiki/identity/

  draft-content:
    load:
      - wiki/identity/persona.md
      - wiki/identity/key-positions.md
      - wiki/theses/
      - runtime/shared/feedback-summary.json
      - runtime/shared/identity-strategy.json
      - wiki/queries/
    protected_writes:
      - wiki/identity/
```

Validation rules:

- top-level `version` required
- top-level `tasks` required
- each task must define `load`
- each task may define `protected_writes`
- all paths are workspace-relative

---

## 5.2 `runtime/shared/resolver-pack.json`

Recommended structure:

```json
{
  "schema": "self-ip-resolver-pack-v1",
  "generated_at": "...",
  "version": "self-ip-resolver-v1",
  "protected_paths": [],
  "tasks": {
    "draft-content": {
      "load": [...],
      "protected_writes": [...],
      "missing": []
    }
  }
}
```

---

## 5.3 `runtime/shared/claim-index.json`

Recommended structure:

```json
{
  "schema": "self-ip-claim-index-v1",
  "generated_at": "...",
  "count": 0,
  "claims": []
}
```

Each claim entry should include:

- id
- statement
- status
- confidence
- domain
- linked_theses
- path
- timestamps

---

## 5.4 `runtime/shared/thesis-index.json`

Recommended structure:

```json
{
  "schema": "self-ip-thesis-index-v1",
  "generated_at": "...",
  "count": 0,
  "theses": []
}
```

Each thesis entry should include:

- id
- title
- status
- confidence
- scope
- revision_state
- supporting_claims
- path

---

## 5.5 `runtime/shared/feedback-summary.json`

Recommended structure:

```json
{
  "schema": "self-ip-feedback-summary-v1",
  "generated_at": "...",
  "topic_resonance": {},
  "format_signals": {},
  "platform_signals": {},
  "recent_feedback": []
}
```

---

## 5.6 `runtime/shared/identity-strategy.json`

Recommended structure:

```json
{
  "schema": "self-ip-identity-strategy-v1",
  "generated_at": "...",
  "topic_weights": {},
  "format_preferences": {},
  "platform_heuristics": {},
  "source_priority": {},
  "protected_identity_fields": [
    "persona",
    "key_positions"
  ]
}
```

---

## 5.7 `runtime/shared/identity-drift-report.json`

Recommended structure:

```json
{
  "schema": "self-ip-identity-drift-report-v1",
  "generated_at": "...",
  "status": "none",
  "findings": [],
  "requires_human_review": false
}
```

---

## 6. Script responsibilities

This section is the deterministic ownership map.

## `scripts/build_resolver_pack.py`

Owner:
- schema → runtime projection

Responsibilities:
- parse YAML
- validate structure
- validate referenced paths
- emit resolver pack JSON

## `scripts/build_claim_index.py`

Owner:
- `wiki/claims/` → runtime index

Responsibilities:
- parse markdown frontmatter
- validate claim schema
- emit compact index JSON

## `scripts/build_thesis_index.py`

Owner:
- `wiki/theses/` → runtime index

Responsibilities:
- parse markdown frontmatter
- validate thesis schema
- emit compact index JSON

## `scripts/build_feedback_summary.py`

Owner:
- `raw/feedback/` → compiled aggregate

Responsibilities:
- aggregate feedback by topic / format / platform
- emit deterministic summary JSON

## `scripts/build_identity_strategy.py`

Owner:
- feedback summary → adaptive strategy

Responsibilities:
- only update allowed adaptive fields
- never touch identity anchors

## `scripts/check_identity_drift.py`

Owner:
- drift detection

Responsibilities:
- compare recent outputs with identity anchors + theses
- emit report, not edits

---

## 7. Guardrails for Claude Dispatch implementation

When delegating this spec to Claude Dispatch, enforce the following:

1. **Do not rewrite protected identity files**
2. **Do not collapse phases into one giant PR-sized change**
3. **Phase-by-phase commits / summaries are preferred**
4. **Every new script must be runnable from workspace root**
5. **Every runtime JSON must have a schema field and generated_at timestamp**
6. **Empty-input cases must succeed gracefully**
7. **Malformed pages should produce explicit validation errors, not silent omission**
8. **Backward compatibility with current wiki runtime should be preserved unless explicitly migrated**

---

## 8. Verification plan

Each phase must end with concrete verification.

## Phase 1 verification

```bash
python3 scripts/build_resolver_pack.py
cat runtime/shared/resolver-pack.json
```

## Phase 2 verification

```bash
python3 scripts/build_claim_index.py
python3 scripts/build_thesis_index.py
cat runtime/shared/claim-index.json
cat runtime/shared/thesis-index.json
```

## Phase 3 verification

```bash
python3 scripts/build_feedback_summary.py
python3 scripts/build_identity_strategy.py
cat runtime/shared/feedback-summary.json
cat runtime/shared/identity-strategy.json
```

## Phase 4 verification

- run one real workflow using resolver-pack
- show which resolver entry was loaded
- prove protected paths are not writable in that flow

## Phase 5 verification

```bash
python3 scripts/check_identity_drift.py
cat runtime/shared/identity-drift-report.json
```

---

## 9. Suggested Claude Dispatch rollout order

Delegate to Claude Dispatch in this order:

### Dispatch 1
Phase 1 only

### Dispatch 2
Phase 2 only

### Dispatch 3
Phase 3 only

### Dispatch 4
Phase 4 only

### Dispatch 5
Phase 5 only

Rationale:

- easier review
- easier rollback
- cleaner QA
- less cross-phase confusion

If Dispatch 1–2 are accepted cleanly, Dispatch 3–5 become much safer.

---

## 10. Non-goals for v2

To avoid scope creep, v2 should explicitly **not** do the following yet:

- full auto-generation of all claims from all historical sources in one pass
- autonomous identity rewrites
- replacing the current runtime contract wholesale
- migrating every current wiki page into a new schema immediately
- forcing embeddings/vector infra into the core path
- making Obsidian the canonical runtime owner

v2 is a **stabilization + formalization + compilation** upgrade, not a ground-up rewrite.

---

## 11. Definition of done

v2 implementation is considered substantially complete when:

- `schema/` exists and is used
- `resolver-pack.json` exists and is used in at least one real operation
- `wiki/claims/` and `wiki/theses/` exist with indexes
- feedback compiles into `feedback-summary.json` and `identity-strategy.json`
- identity anchors remain protected
- identity drift can be reported deterministically
- all new scripts are runnable and documented

---

## 12. Dispatch-ready summary

If handing to a coding agent, the concise brief is:

> Implement the self-IP LLM Wiki v2 spec incrementally on top of the current workspace. Preserve raw/wiki/runtime local-first architecture and protected identity anchors. Build explicit schema + resolver infrastructure first, then claims/theses, then feedback compilation, then resolver integration, then identity drift reporting. Validate every phase with runnable scripts and emitted runtime JSON artifacts.
