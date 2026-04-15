# Troubleshooting Guide

## Common Issues

### Agent Not Starting

**Symptom**: `dev-claude.sh` exits immediately or status stays "idle"

**Check**:
1. Verify credentials: `cat ~/.config/tagclaw/credentials.json | python3 -m json.tool`
2. Check runtime dirs exist: `ls runtime/{main,bookmarker,trader,shared}/`
3. Verify Python 3.10+: `python3 --version`
4. Check logs: `cat runtime/dev/status.json`

### Dashboard Not Loading

**Symptom**: `http://localhost:7890` returns connection refused

**Check**:
1. Is FastAPI installed? `pip3 install fastapi uvicorn`
2. Is the port in use? `lsof -i :7890`
3. Start manually: `OPENCLAW_WORKSPACE=~/.openclaw/workspace python3 dashboard/server.py`
4. Check dashboard logs: `cat ~/.openclaw/workspace/logs/dashboard.log`
5. Verify all deps: `python3 -c "import fastapi; import uvicorn; print('OK')"`

### Wiki Lint Failing

**Symptom**: `wiki_lint.py` reports broken links or stale pages

**Fix**:
1. Run `python3 scripts/wiki_lint.py` to see the report
2. Check `wiki/lint/latest-report.md` for details
3. Fix broken [[wikilinks]] — ensure target pages exist in `wiki/concepts/`
4. Update stale pages (>30 days without update)
5. Add orphan pages to `wiki/INDEX.md`

### Strategy Not Improving

**Symptom**: Win rate stays below 30% after 20+ cycles

**Check**:
1. Run `python3 scripts/select_strategy.py --stats`
2. Look at worst_combinations — are bad params being avoided?
3. Check if TAS sources are updating (social/trade data flowing)
4. Verify strategy logs have delta values: `tail -5 memory/main-strategy-log.jsonl`

### Contract Verification Failures

**Symptom**: `verify_wiki_contract.py` shows degraded status

**Common fixes**:
- **Source not found**: Create missing raw/ directories
- **Stale derived artifact**: Re-run the generating script
- **Schema mismatch**: Check JSON structure matches expected keys
- **Registry inconsistency**: Run `python3 -c "from scripts.wiki_registry import validate_registry; print(validate_registry())"`

### Cron Jobs Not Running

**Check**:
1. List active crons: `crontab -l`
2. Verify workspace path in cron commands
3. Check cron log: `/var/log/cron` or `journalctl -u cron`
4. Test the command manually first

## Health Check Commands

```bash
# Overall system health
python3 scripts/verify_wiki_contract.py

# Wiki health
python3 scripts/wiki_lint.py

# Strategy status
python3 scripts/select_strategy.py --stats
python3 scripts/record_strategy_cycle.py

# Dashboard health
curl -s http://localhost:7890/api/health | python3 -m json.tool
```

## Getting Help

- Check `docs/` for component-specific guides
- Review `schema/` for operational rules
- File issues at the repo's GitHub Issues page
