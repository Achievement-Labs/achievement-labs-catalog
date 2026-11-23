# Signing keys

Only the public key lives here:

    keys/catalog-signing-public.pem

It is the ECDSA P-256 public key (PEM, SubjectPublicKeyInfo) matching the `CATALOG_SIGNING_KEY`
repository secret. `publish.yml` verifies every published `v1/manifest.json.sig` against it and
fails if this file is missing. Clients embed the same public key.

The private key never goes in this repository: not in commits, pull requests, issues, workflow
logs or build output. It exists only as the `CATALOG_SIGNING_KEY` secret. To rotate, generate a
new key pair outside the repository, update the secret, then replace the public key here in the
same change that ships the new key to clients.

Tests generate ephemeral keys in memory and never write them here.
