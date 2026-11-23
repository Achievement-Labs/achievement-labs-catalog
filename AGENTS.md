# Project instructions

These data rules come from the Achievement Labs app repository's AGENTS.md. They are explicit
user requirements, and `tools/validate.py` enforces them.

## Catalog layout

- The catalog uses `titles/index.json` plus `titles/<titleId>/Data.json` and
  `titles/<titleId>/<titleId>.json` (with an optional `titles/<titleId>/Stats.json`). Keep each
  title in its own folder.
- Per-title Data.json contains exactly its numeric title block followed by `SupportedTitleIDs`.
- The index contains runtime title summaries without achievement mappings, with
  `SupportedTitleIDs` last. The index must match the title folders and each Data.json title block.
- Never put research or diagnostic fields in either file. Title blocks hold only TitleId,
  TitleName, Devices, ServiceConfigId, Enabled, FullySupported, PartiallySupported and
  Achievements.

## Data.json ordering

- All title blocks must appear above `SupportedTitleIDs`. Keep `SupportedTitleIDs` as the final
  top-level property. Never append a title below it.
- After every Data.json or index.json edit or generation, verify that `SupportedTitleIDs` is last and
  that zero numeric title keys occur below it (`python tools/validate.py`).
- Reordering must preserve every title block, achievement mapping, metadata value and supported ID.
  Preserve unrelated edits.
- Apply this rule to scripts that generate or update catalog files as well as to direct edits.

## Event recipes

- Do not generate RawBody or TemplateReplacement operations, and do not embed full templates in
  achievement recipes.
- Keep test patterns simple. Exclude randomized multi-stat patterns involving map IDs, kills,
  mission scores, time played, multipliers and LDAP timestamps.
- Preserve preexisting unrelated title recipes when enforcing these rules on new testing work.
- Every recipe must render: replacing each Target in the template leaves no `REPLACE*`
  placeholder except REPLACESEQ, REPLACETIME, REPLACEXUID and REPLACESESSIONGUID, and the result
  is valid JSON for the same title.

## Files

- UTF-8 without a BOM, LF line endings, and a final newline in every file.

## Secrets and repository routing

- Never commit or print signing keys, keyvaults, personal profiles or account secrets. The manifest
  signing key exists only as the `CATALOG_SIGNING_KEY` repository secret. Tests generate ephemeral
  keys in memory.
- This repository is the event data source for Windows, Mac, Linux and mobile. Existing released
  mobile clients keep the legacy `Achievement-Labs/achievement-labs` `mobile-event-feed` endpoint
  until a bridge release. Do not change that endpoint from here.
- Never point an official build's catalog feed at a fork.
