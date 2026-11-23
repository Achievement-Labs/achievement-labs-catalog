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
  parse as JSON, and `data.baseData.titleId` must match the title. A trailing comma (which the
  app's Newtonsoft parser accepts) is reported as a warning, not an error.

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

- `revision` is `git rev-list --count HEAD` on main, so it only increases.
- A title's `revision` is the catalog revision of the last commit that touched `titles/<id>/`.
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
it is missing. Once a public key is chosen, commit it as `keys/catalog-signing-public.pem` and
publish.yml cross-checks every signature.

## Mobile feed

`tools/build_mobile.py` (run by `build.py`) ports the app repository's
`tools/mobile/publish_events.py` and `export_event_pack.py` and writes the same formats:

- `https://achievement-labs.github.io/achievement-labs-catalog/mobile/catalogue.json`:
  additions not bundled in the apps (titles listed in `mobile/bundled-titles.json` are left out).
- `https://achievement-labs.github.io/achievement-labs-catalog/mobile/EventPack.json`: the
  offline event pack, every title that fits the conservative limits all platforms accept.

Here `upstreamRelease` is `catalog-r<revision>`, `archiveSHA256` is the SHA-256 of
`v1/manifest.json`, and `generatedAt` is the commit time. Both files must stay within the
clients' 32 MiB download limit; the build fails rather than exceed it.

### Legacy mobile endpoint (unchanged)

Existing Android and iOS clients read
`https://raw.githubusercontent.com/Achievement-Labs/achievement-labs/mobile-event-feed/catalogue.json`
(and `.../mobile-event-feed/EventPack.json` for manual import). That branch is still produced by
the app repository's mobile-feed workflow from release `Events.zip` assets. It stays in place until
a bridge release moves mobile clients to this repository. This repository does not write to it.
