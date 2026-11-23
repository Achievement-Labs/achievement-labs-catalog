"""Sign dist/v1/manifest.json with ECDSA P-256 / SHA-256.

CATALOG_SIGNING_KEY=<PEM private key> python tools/sign.py [--manifest PATH]

Writes <manifest>.sig: base64 of the IEEE P1363 signature (32-byte r || 32-byte s) over the
exact manifest bytes. The key is read only from the environment and is never printed.
"""
import argparse
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

ROOT = Path(__file__).resolve().parents[1]


def load_private_key(pem):
    key = serialization.load_pem_private_key(pem.encode() if isinstance(pem, str) else pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('CATALOG_SIGNING_KEY must be an ECDSA P-256 private key')
    return key


def sign_bytes(key, data):
    r, s = decode_dss_signature(key.sign(data, ec.ECDSA(hashes.SHA256())))
    return base64.b64encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'dist' / 'v1' / 'manifest.json')
    args = parser.parse_args(argv)
    pem = os.environ.get('CATALOG_SIGNING_KEY', '')
    if not pem.strip():
        print('error: CATALOG_SIGNING_KEY is not set; refusing to publish an unsigned manifest', file=sys.stderr)
        return 2
    try:
        key = load_private_key(pem)
    except ValueError as error:
        # Do not echo the key or the parser's view of it.
        print('error: CATALOG_SIGNING_KEY is not a usable unencrypted P-256 PEM key: ' + type(error).__name__, file=sys.stderr)
        return 2
    signature = sign_bytes(key, args.manifest.read_bytes())
    Path(str(args.manifest) + '.sig').write_bytes(signature + b'\n')
    print(f'signed {args.manifest.name} ({len(signature)} base64 chars)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
