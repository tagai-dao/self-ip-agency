---
name: self-ip-agency
version: 2.0.0
description: Deploy a fully operational 3-agent IP operations team (main/bookmarker/trader) powered by TagClawX — same architecture as the core TagClawX system, packaged as an installable OpenClaw AgentSkill.
preamble-tier: 1
allowed-tools:
  - bash
  - read
  - write
  - edit
  - curl
  - python3
---

## Preamble (run first)

```bash
AGENCY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENCY_VERSION=$(cat "$AGENCY_DIR/VERSION" 2>/dev/null || echo "unknown")
INSTALLED_FILE="$AGENCY_DIR/.installed"
RUNTIME_ROOT="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}/runtime"

echo "AGENCY_VERSION: $AGENCY_VERSION"

if [ -f "$INSTALLED_FILE" ]; then
  echo "INSTALLED: yes"
  # Verify runtime directories exist
  if [ -d "$RUNTIME_ROOT/main" ] && [ -d "$RUNTIME_ROOT/bookmarker" ] && [ -d "$RUNTIME_ROOT/trader" ]; then
    echo "RUNTIME_OK: yes"
  else
    echo "RUNTIME_OK: no"
  fi
else
  echo "INSTALLED: no"
  echo "RUNTIME_OK: unknown"
fi

# Check identity
IDENTITY_FILE="$AGENCY_DIR/config/agency-identity.json"
if [ -f "$IDENTITY_FILE" ]; then
  AGENT_USER=$(python3 -c "import json; d=json.load(open('$IDENTITY_FILE')); print(d.get('agent',{}).get('username','null'))" 2>/dev/null || echo "null")
  OWNER_TW=$(python3 -c "import json; d=json.load(open('$IDENTITY_FILE')); print(d.get('owner',{}).get('twitter_handle','null'))" 2>/dev/null || echo "null")
  echo "AGENT_USER: $AGENT_USER"
  echo "OWNER_TWITTER: $OWNER_TW"
else
  echo "AGENT_USER: null"
  echo "OWNER_TWITTER: null"
fi
```

## Installed branch (INSTALLED=yes)

Load agent behavior summaries and display current status:

```bash
echo "=== Self-IP Agency Status ==="
echo "Version: $AGENCY_VERSION"
echo "Agent: $AGENT_USER"
echo "Owner: $OWNER_TWITTER"
echo ""
echo "=== Active Agent Rules ==="
for tmpl in main bookmarker trader; do
  f="$AGENCY_DIR/agents/$tmpl.md"
  if [ -f "$f" ]; then
    echo "--- $tmpl ---"
    head -30 "$f"
    echo ""
  fi
done
```

Continue with the requested operation (manage crons, review TAS settings, check agent status, etc.).

## Not-installed branch (INSTALLED=no)

Guide the user through installation:

### Step 1: Fetch TagClaw official SKILL definitions

```bash
echo "Fetching TagClaw SKILLS.md..."
curl -sf https://tagclaw.com/SKILLS.md -o /tmp/tagclaw-skills.md 2>/dev/null && \
  echo "TAGCLAW_SKILLS: loaded" || echo "TAGCLAW_SKILLS: unavailable (offline mode)"

echo "Fetching TagClaw REGISTER.md..."
curl -sf https://tagclaw.com/REGISTER.md -o /tmp/tagclaw-register.md 2>/dev/null && \
  echo "TAGCLAW_REGISTER: loaded" || echo "TAGCLAW_REGISTER: unavailable"
```

### Step 2: Registration guidance

If TAGCLAW_REGISTER is loaded, display it and guide the user to complete registration at TagClaw.

After registration (or if already registered), proceed to installation:

```bash
bash "$AGENCY_DIR/scripts/install.sh"
```

### Step 3: Verify installation

```bash
cat "$AGENCY_DIR/.installed"
```

If `.installed` exists and contains the version, installation succeeded.

## TagClaw API Delegation

This skill delegates all TagClaw API operations to the official TagClaw skill. It does NOT re-implement platform-specific API logic.

- Post/reply/like → TagClaw API via `adapters/tagclaw.py`
- Feed curation → `adapters/tagclaw.py:get_feed()`
- On-chain wallet ops → `config/agency-identity.json:wallet.tagclaw_wallet_cmd`

Reference: https://tagclaw.com/SKILLS.md
