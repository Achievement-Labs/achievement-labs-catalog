# Achievement Labs event catalog

The single source of event recipes for Achievement Labs on Windows, Mac, Linux, Android and iOS.
It is published on its own schedule, independently of app releases: a merged fix is live in minutes.

Published manifest:

    https://achievement-labs.github.io/achievement-labs-catalog/v1/manifest.json

## Layout

```
titles/
  index.json                 runtime title summaries (no achievement mappings), SupportedTitleIDs last
  <titleId>/Data.json        {"<titleId>": {title block}, "SupportedTitleIDs": ["<titleId>"]}
  <titleId>/<titleId>.json   the event template
  <titleId>/Stats.json       stat recipes, where the title has them
mobile/bundled-titles.json   title IDs already bundled in the apps (left out of mobile catalogue.json)
mobile/pack-assignment.json  which offline pack part holds each title (kept stable; see Mobile feed)
tools/                       validate, build, sign and verify scripts (Python 3.12)
tests/                       unit tests
dist/                        build output (not committed)
```

The data rules are in [AGENTS.md](AGENTS.md). `tools/validate.py` enforces them.

## Fixing a recipe

1. Edit `titles/<titleId>/Data.json` (or the template `titles/<titleId>/<titleId>.json`). Keep the
   file UTF-8 with LF line endings and a final newline. If you change TitleName, Enabled,
   FullySupported, PartiallySupported, Devices, ServiceConfigId or TitleId, make the same change
   in `titles/index.json`.
2. Check it locally:

   ```
   python tools/validate.py
   python -m unittest discover -s tests
   python tools/build.py
   ```

3. Open a pull request. CI (`.github/workflows/ci.yml`) validates the catalog, renders every
   recipe, runs the unit tests and builds twice to prove the output is reproducible.
4. Merge. `.github/workflows/publish.yml` validates, builds, signs the manifest and deploys
   `dist/` to GitHub Pages. Clients pick up the new manifest within minutes, with no app release.

## Validation

`tools/validate.py` checks that:

- every Data.json holds exactly its title block followed by SupportedTitleIDs, which is last;
- `index.json` has SupportedTitleIDs last, no achievement mappings, and matches the title folders
  and each Data.json title block;
- no RawBody or TemplateReplacement operations exist, and title blocks hold only runtime fields;
- every file is UTF-8 without a BOM, uses LF, and ends with `\n`;
- every recipe renders. The renderer mirrors the app (`EventCatalog.cs`): each operation's
  Target is replaced in the template, then REPLACESEQ, REPLACETIME, REPLACEXUID and
  REPLACESESSIONGUID are filled. No other `REPLACE*` placeholder may remain, the result must
  parse as strict JSON (no trailing commas, even though the app's Newtonsoft parser accepts
  them), and `data.baseData.titleId` must match the title.

## Manifest format (v1)

`dist/v1/manifest.json` is UTF-8 with LF line endings and sorted keys:

```json
{
  "commit": "<git sha of the build>",
  "generatedUtc": "<commit time, UTC, e.g. 2026-11-23T17:42:38Z>",
  "revision": 123,
  "schema": 1,
  "titles": {
    "1145574011": {
      "achievements": 50,
      "enabled": true,
      "fullySupported": true,
      "name": "Titanfall 2",
      "partiallySupported": false,
      "path": "titles/1145574011.9bc3cc640e98.json",
      "revision": 120,
      "sha256": "9bc3cc640e98...",
      "size": 150599
    }
  }
}
```

- `revision` is `git rev-list --count --first-parent HEAD` on main, so it only increases.
- A title's `revision` is the catalog revision of the last first-parent (main) commit that touched
  `titles/<id>/`; a merge counts as touching what it brought in. It is never above `revision`.
- `generatedUtc` is the commit time, so two builds of the same commit are byte-identical.
- `path` (relative to `v1/`) is a content-addressed bundle; `sha256` and `size` describe its exact
  bytes. Bundles never change once published, so they can be cached indefinitely. Only the
  manifest needs revalidating.
- Disabled titles appear with `"enabled": false` and no `path`, `sha256` or `size`.

A bundle is compact JSON:

```json
{"data": {title block from Data.json}, "revision": 120, "stats": {Stats.json title object} | null,
 "template": "<raw template text>", "titleId": "1145574011"}
```

### Signature

`v1/manifest.json.sig` is the base64 IEEE P1363 (r||s, 64 bytes) ECDSA P-256 / SHA-256
signature over the exact bytes of `manifest.json`. Clients verify it with the public key before
trusting the manifest, then check each bundle's `sha256`. To check by hand:

    python tools/verify_signature.py catalog-signing-public.pem --manifest dist/v1/manifest.json

The private key exists only as the `CATALOG_SIGNING_KEY` repository secret; `publish.yml` fails if
it is missing. The matching public key lives at `keys/catalog-signing-public.pem`
(see [keys/README.md](keys/README.md)); publish.yml verifies every signature against it and fails
if the file is missing.

## Mobile feed

`tools/build_mobile.py` (run by `build.py`) ports the app repository's
`tools/mobile/publish_events.py` and `export_event_pack.py` and writes the same formats:

- `https://achievement-labs.github.io/achievement-labs-catalog/mobile/catalogue.json`:
  additions not bundled in the apps (titles listed in `mobile/bundled-titles.json` are left out).
- `https://achievement-labs.github.io/achievement-labs-catalog/mobile/EventPack.json`: part 1 of
  the offline event pack, which holds every title that fits the conservative limits all platforms
  accept. Further parts are listed in `EventPackIndex.json` (see below).

### Offline pack parts

The offline pack is split into parts so that every file stays within the 32 MiB download limit:

```
dist/mobile/EventPack.json            part 1 (the original file name, unchanged for existing readers)
dist/mobile/EventPack-2.json          part 2, then EventPack-3.json, ... (same shape as part 1)
dist/mobile/EventPackIndex.json       {"schema":1,"revision":<catalog revision>,
                                       "parts":[{"file":"EventPack.json","sha256":...,"size":...,"titles":<count>}, ...]}
dist/mobile/EventPackIndex.json.sig   signature over EventPackIndex.json (same key and format as manifest.json.sig)
```

Every part has the same top-level fields as the catalogue (`schemaVersion`, `upstreamRelease`,
`archiveSHA256`, `generatedAt`, `titles`), and a title appears in exactly one part. Clients read
`EventPackIndex.json`, verify its signature, then fetch each part and check its `sha256` and `size`.

Assignment is stable and lives in the committed `mobile/pack-assignment.json`
(`{"schema":1,"parts":[["<titleId>", ...], ...]}`):

- titles already assigned keep their part and order, so adding titles never reshuffles parts;
- titles not yet assigned are added in ascending numeric title ID to the last part while it stays
  within 30 MiB, otherwise a new part is opened;
- titles that leave the pack (disabled, or no recipe passes the pack limits) are removed from it.

`tools/build.py` writes newly assigned titles back to `mobile/pack-assignment.json` and prints
`updated pack-assignment.json`. Commit that file with the title change. CI and publish build with
`--check`, which fails instead of writing when the assignment would change. Part 1 kept its original
576 titles, which already exceed the 30 MiB target (a warning is printed), so new titles start in
part 2. The 32 MiB limit is enforced per file and fails the build.

Here `upstreamRelease` is `catalog-r<revision>`, `archiveSHA256` is the SHA-256 of
`v1/manifest.json`, and `generatedAt` is the commit time. Every file must stay within the
clients' 32 MiB download limit; the build fails rather than exceed it.

### Legacy mobile endpoint (unchanged)

Existing Android and iOS clients read
`https://raw.githubusercontent.com/Achievement-Labs/achievement-labs/mobile-event-feed/catalogue.json`
(and `.../mobile-event-feed/EventPack.json` for manual import). That branch is still produced by
the app repository's mobile-feed workflow from release `Events.zip` assets. It stays in place until
a bridge release moves mobile clients to this repository. This repository does not write to it.
