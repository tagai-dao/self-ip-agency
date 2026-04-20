---
title: Guided X Sync Implementation Checklist
status: ready-for-build
updated: 2026-04-20
owner: self-ip-agency
---

# Guided X Sync — Implementation Checklist

## Goal

After a user installs self-IP Agency, the installer should be able to bootstrap the owner's recent X data into `raw/x-tweets/` without requiring X API keys or manual cookie/token setup.

The only user-facing requirement should be:
- provide / confirm the owner's X handle
- complete one guided X login / account-guidance step if needed

Then the system should:
1. discover the owner's tweets + replies from the past 3 days
2. write normalized raw artifacts into `raw/x-tweets/`
3. compile them into wiki synthesis artifacts
4. surface truthful status in installer outputs / next steps / verification

## Canonical path

- preferred: browser-guided URL discovery
- current practical zero-credential bootstrap: browser-guided manifest if available, otherwise public RSS discovery + per-tweet structured fetch
- never require X API keys for the default install path
- never require users to manually copy cookies / auth tokens for the default path
