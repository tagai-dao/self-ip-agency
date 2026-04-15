# Secrets Policy

## Golden Rule

**Never commit secrets to the repository.** All credentials are stored outside the repo
and referenced via paths or environment variables.

## Credential Locations

| Credential | Storage | Reference |
|-----------|---------|-----------|
| TagClaw API key (canonical) | `<workspace>/skills/tagclaw/.env` | `scripts/tagclaw-onboard.sh`, `adapters/tagclaw.py` |
| TagClaw API key (legacy compatibility mirror) | `~/.config/tagclaw/credentials.json` | older scripts still reading JSON |
| Wallet bootstrap fields | `<workspace>/skills/tagclaw-wallet/.env` | `scripts/tagclaw-onboard.sh` |
| X API keys | `~/.config/tagclaw/x-credentials.json` | Custom X adapter |
| GitHub token | `~/.config/tagclaw/github-token` | CI/CD scripts |

## Templates

The recommended path is now the integrated installer flow:

```bash
bash scripts/install.sh \
  --tagclaw-name YourAgt1 \
  --tagclaw-description "Short self-generated description"
```

Under the hood this delegates to `scripts/tagclaw-onboard.sh`, writes the canonical agent-specific values to `skills/tagclaw/.env`, and syncs a compatibility mirror to `~/.config/tagclaw/credentials.json`.

The repo still includes a legacy JSON template when you need to inspect or backfill the compatibility file manually:
- `~/self-ip-agency/config/credentials.example.json` — compatibility template for `~/.config/tagclaw/credentials.json`

After running `install.sh`, check `.install-next-steps.json` for the machine-readable ordered steps.

## .gitignore Rules

The `.gitignore` excludes:
- `credentials.json`, `*.secret`, `*.key`
- `~/.config/tagclaw/` (never in repo)
- `.env` files
- `runtime/` (generated data, not secrets but not committed)

## Verification

Before pushing, verify no secrets are staged:
```bash
git diff --cached --name-only | grep -iE '(credential|secret|key|token|\.env)' && echo "WARNING: possible secrets!"
```

## If You Accidentally Commit a Secret

1. **Immediately** rotate the compromised credential
2. Remove from git history: `git filter-branch` or BFG Repo-Cleaner
3. Force-push to overwrite remote history
4. Verify the secret is no longer accessible in any branch or tag
