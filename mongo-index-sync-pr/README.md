# MongoDB Index Synchronization — Upstream PR Plan

## Goal
Contribute a small, reviewable improvement to `adaliafoundation/influence-server` so a fresh Influence database can be initialized reliably before workers start.

## Background
During our Influence testnet fresh-database bootstrap, missing MongoDB indexes prevented a reliable startup. We investigated the issue and identified the existing `sync_mongo_indexes.js` script as a required setup step. The upstream maintainer confirmed this belongs in `adaliafoundation/influence-server`, moved the GitHub issue there, and welcomed a pull request.

## Requested scope (upstream maintainer feedback)
- [ ] Add an explicit npm script for the existing MongoDB index synchronization script.
- [ ] Document that index synchronization is required **before starting workers** when setting up a fresh database.
- [ ] Include instructions for both local and Docker usage.
- [ ] Clearly warn that synchronization may remove indexes not defined in the application's models.
- [ ] Ensure the script exits with a **nonzero exit code** if synchronization fails.
- [ ] Verify against a fresh disposable MongoDB database with automatic indexing disabled.
- [ ] Run synchronization a second time and verify that it succeeds (repeatability/idempotency).
- [ ] Submit a focused pull request to `adaliafoundation/influence-server` and address review feedback.

## Approach
1. Confirm ArisAI has write access to our fork `f-r-e-d-eth/influence-server`.
2. Inspect upstream `package.json`, `sync_mongo_indexes.js`, and setup documentation before editing.
3. Create a feature branch in our fork (proposed: `fix/explicit-mongo-index-sync`).
4. Implement only the requested script, command, and documentation changes.
5. Test in an isolated disposable database, **never** against our live testnet or mainnet databases.
6. Record commands, outcomes, and relevant log excerpts in this helper folder.
7. Open the upstream PR and link the original issue.

## Notes
- Keep index synchronization explicit; do **not** silently run it on every server startup.
- Do not assume the final npm script name or Docker commands until inspecting the repository.
- Prefer one step at a time, with reproducible test commands and clearly documented prerequisites.

## Links
- [Our influence-server fork](https://github.com/f-r-e-d-eth/influence-server)
- [Upstream influence-server](https://github.com/adaliafoundation/influence-server)
- [Influence issues](https://github.com/adaliafoundation/influence-issues)
