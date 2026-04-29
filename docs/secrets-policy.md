# Secrets Policy

## Golden Rule

**Never commit secrets to the repository.** TagClaw API state lives in the skill folder, and wallet secrets live in the wallet skill folder.

## Credential Locations

| Credential | Storage | Reference |
|-----------|---------|-----------|
| TagClaw API key | `<workspace>/skills/tagclaw/.env` | `scripts/tagclaw-onboard.sh`, `adapters/tagclaw.py`, runtime scripts |
| Wallet bootstrap + private key material | `<workspace>/skills/tagclaw-wallet/.env` | upstream wallet setup + trader runtime |
| X API keys | `<workspace>/skills/tagclaw/x-credentials.json` | Custom X adapter |
| GitHub token | `<workspace>/skills/tagclaw/github-token` | CI/CD scripts |

## Templates

The recommended path is the integrated installer flow:

```bash
bash scripts/install.sh
```

You may still override the derived onboarding values explicitly:

```bash
bash scripts/install.sh \
  --tagclaw-name YourAgt1 \
  --tagclaw-description "Short self-generated description"
```

Under the hood this delegates to `scripts/tagclaw-onboard.sh`, which writes real values to `skills/tagclaw/.env` only after the register step succeeds. It does **not** pre-create a placeholder skill `.env` before onboarding.

`config/credentials.example.json` is retained only as a deprecated migration note; it is no longer a runtime credential source.

After running `install.sh`, check `.install-next-steps.json` for the machine-readable ordered steps.

## .gitignore Rules

The `.gitignore` excludes:
- `credentials.json`, `*.secret`, `*.key`
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
