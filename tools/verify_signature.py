"""Verify manifest.json.sig against a P-256 public key PEM file.

python tools/verify_signature.py PUBLIC_KEY.pem [--manifest PATH] [--signature PATH]
Exit 0 when the signature is valid.
"""
import argparse
import base64
import binascii
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

ROOT = Path(__file__).resolve().parents[1]


def verify_bytes(public_key, data, signature_b64):
    try:
        raw = base64.b64decode(signature_b64.strip(), validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(raw) != 64:
        return False
    der = encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big'))
    try:
        public_key.verify(der, data, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def load_public_key(pem):
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('public key must be ECDSA P-256')
    return key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('public_key', type=Path)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'dist' / 'v1' / 'manifest.json')
    parser.add_argument('--signature', type=Path)
    args = parser.parse_args(argv)
    signature = (args.signature or Path(str(args.manifest) + '.sig')).read_bytes()
    if verify_bytes(load_public_key(args.public_key.read_bytes()), args.manifest.read_bytes(), signature):
        print('ok: signature valid')
        return 0
    print('error: signature INVALID', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
