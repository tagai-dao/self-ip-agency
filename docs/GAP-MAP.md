# Self-IP Agency — Gap Map & Implementation Plan

> Generated 2026-04-15 from production workspace audit.
> Source of truth: `~/.openclaw/workspace` (live TagClawX deployment)

## Current State (v1.0.0 scaffold)

| Component | Status | Notes |
|-----------|--------|-------|
| 3 agent templates | ✅ Present | main/bookmarker/trader .md.tmpl |
| Basic config | ✅ Present | agency.config.yaml, identity, crons, agents |
| Platform adapters | ✅ Present | base.py + tagclaw.py (stdlib-only) |
| Install/uninstall | ✅ Present | Idempotent, atomic writes |
| Basic dashboard | ⚠️ Outdated | Simple TAS hero + 3 cards, no wiki/strategy |
| Runtime templates | ⚠️ Incomplete | Missing wiki, strategy, community artifacts |
| Self-IP LLM Wiki | ❌ Missing | No schema, scripts, templates, Obsidian guide |
| AutoResearch/TAS | ❌ Missing | No strategy optimizer, A/B testing, hill-climbing |
| X account setup | ❌ Missing | No documentation |
| Obsidian setup | ❌ Missing | No documentation |
| Credential templates | ❌ Missing | No .example files |
| Troubleshooting | ❌ Missing | No operator guidance |
| Deployment guide | ❌ Missing | README is quickstart only |

---

## Implementation Plan

### Phase 1: Wiki System Integration
**Priority: Critical** — The self-IP LLM Wiki is the knowledge backbone.

- [ ] Add `schema/` directory (resolver-map.yaml, rules, identity-safety)
- [ ] Add `wiki-template/` with starter concepts and structure
- [ ] Add wiki scripts (lint, utils, registry, search, ingest, verify)
- [ ] Add `config/wiki_topic_registry.json` template
- [ ] Add runtime templates for wiki artifacts
- [ ] Write Obsidian setup guide (`docs/obsidian-setup.md`)
- [ ] Write wiki operation guide (`docs/wiki-guide.md`)

### Phase 2: AutoResearch Framework
**Priority: Critical** — Strategy optimization is core to autonomous operation.

- [ ] Add strategy scripts (select, experiment, record, normalize, publish)
- [ ] Add runtime templates for strategy artifacts
- [ ] Add strategy log schema and examples
- [ ] Write AutoResearch guide (`docs/autoresearch-guide.md`)

### Phase 3: Dashboard Cutover
**Priority: High** — Current dashboard is too basic for production.

- [ ] Replace `dashboard/` with rich viz from production
- [ ] Add TAS Command Center, portfolio viz, community heatmap
- [ ] Parameterize paths (env vars, not hardcoded)
- [ ] Update dashboard README

### Phase 4: Configuration & Credentials
**Priority: High** — Enable safe deployment.

- [ ] Create credential templates (`.example` files)
- [ ] Document secrets policy
- [ ] Add env var configuration guide
- [ ] Update `config/` with wiki and strategy configs

### Phase 5: Documentation Overhaul
**Priority: High** — Make the repo the deployment entrypoint.

- [ ] Rewrite README.md as comprehensive deployment guide
- [ ] Write X account setup guide (`docs/x-setup.md`)
- [ ] Write troubleshooting guide (`docs/troubleshooting.md`)
- [ ] Write operator runbook (`docs/operator-guide.md`)
- [ ] Update SKILL.md for new capabilities

### Phase 6: Installer Update
**Priority: Medium** — Install script must cover new components.

- [ ] Add wiki installation steps
- [ ] Add autoresearch initialization
- [ ] Add new dashboard deployment
- [ ] Update runtime template copying
- [ ] Add Obsidian detection/setup prompt

---

## Architecture (Target State)

```
self-ip-agency/
├── README.md                    ← Deployment entrypoint
├── SKILL.md                     ← Agent skill preamble
├── VERSION                      ← Semantic version
├── config/
│   ├── agency.config.yaml       ← Main config (TAS, modes, platform)
│   ├── agency-identity.json     ← Template: agent/owner/wallet binding
│   ├── cron-jobs.json           ← Cron schedules
│   ├── openclaw-agents.yaml     ← Agent registration
│   ├── wiki_topic_registry.json ← Topic canonicalization (template)
│   └── credentials.example.json ← Credential template (NO secrets)
├── schema/                      ← Operational constitution (NEW)
│   ├── resolver-map.yaml        ← Task-to-context contracts
│   ├── ingest-rules.md          ← Wiki ingestion rules
│   ├── query-rules.md           ← Query resolution rules
│   ├── publish-rules.md         ← Content publishing rules
│   ├── lint-rules.md            ← Wiki health check rules
│   ├── thesis-rules.md          ← Claim/thesis lifecycle
│   ├── feedback-rules.md        ← Feedback compilation
│   ├── identity-safety.md       ← Protected boundaries
│   └── artifact-routing.md      ← Derived artifact routing
├── agents/                      ← Agent behavior templates
│   ├── main.md.tmpl
│   ├── bookmarker.md.tmpl
│   └── trader.md.tmpl
├── adapters/                    ← Platform integration
│   ├── base.py
│   └── tagclaw.py
├── scripts/
│   ├── install.sh               ← Updated installer
│   ├── uninstall.sh
│   ├── lib/common.sh
│   ├── wiki_lint.py             ← Wiki health checks (NEW)
│   ├── wiki_utils.py            ← Wiki utilities (NEW)
│   ├── wiki_registry.py         ← Topic resolver (NEW)
│   ├── wiki_search.py           ← Wiki search (NEW)
│   ├── verify_wiki_contract.py  ← Contract verifier (NEW)
│   ├── build_wiki_brief.py      ← Execution brief builder (NEW)
│   ├── build_topic_heatmap.py   ← Topic heatmap (NEW)
│   ├── refresh_community_heat.py← Community heat (NEW)
│   ├── select_strategy.py       ← Strategy optimizer (NEW)
│   ├── strategy_experiment.py   ← A/B testing (NEW)
│   ├── record_strategy_cycle.py ← Cycle recorder (NEW)
│   ├── normalize_experiment.py  ← Normalization (NEW)
│   └── publish_tas_social.py    ← TAS publisher (NEW)
├── dashboard/                   ← Rich monitoring (REPLACED)
│   ├── README.md
│   ├── requirements.txt
│   ├── server.py                ← FastAPI with wiki+strategy endpoints
│   └── static/
│       ├── index.html           ← TAS Command Center UI
│       ├── style.css            ← Dark theme
│       └── app.js               ← Real-time viz
├── wiki-template/               ← Starter wiki structure (NEW)
│   ├── concepts/
│   │   └── _example.md
│   ├── identity/
│   │   ├── persona.md
│   │   └── key-positions.md
│   ├── synthesis/
│   ├── queries/
│   ├── execution/
│   └── INDEX.md
├── runtime-template/            ← Extended runtime templates
│   ├── main/
│   ├── bookmarker/
│   ├── trader/
│   └── shared/                  ← +wiki, strategy, community artifacts
├── docs/                        ← Comprehensive documentation (NEW)
│   ├── deployment-guide.md
│   ├── wiki-guide.md
│   ├── autoresearch-guide.md
│   ├── obsidian-setup.md
│   ├── x-setup.md
│   ├── troubleshooting.md
│   ├── operator-guide.md
│   ├── secrets-policy.md
│   └── wiki-runtime-contract.md
└── .gitignore                   ← Secrets exclusion
```
