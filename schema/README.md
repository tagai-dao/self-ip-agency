# schema/

Operational rules and resolver definitions for the self-IP LLM Wiki v2.

This directory makes implicit wiki operating rules explicit, loadable, and versionable.

## Contents

| File | Purpose |
|------|---------|
| `resolver-map.yaml` | Task-to-context contract: what files each operation loads |
| `ingest-rules.md` | Rules for ingesting raw sources into the wiki |
| `query-rules.md` | Rules for answering queries against the wiki |
| `lint-rules.md` | Rules for wiki health checks and linting |
| `publish-rules.md` | Rules for content drafting and publication |
| `feedback-rules.md` | Rules for processing feedback into strategy |
| `thesis-rules.md` | Rules for claim/thesis lifecycle management |
| `identity-safety.md` | Identity protection boundary definitions |
| `artifact-routing.md` | Rules for routing derived artifacts |

## Relationship to runtime

The resolver map (`resolver-map.yaml`) is compiled into `runtime/shared/resolver-pack.json` by `scripts/build_resolver_pack.py`. The pack validates that referenced paths exist and is consumed by downstream operations.

## Design principle

> Skills define **how** to think; resolvers define **what to load**.

Schema docs are the "constitution" — they change slowly and deliberately. Runtime artifacts are projections that change on every build.
