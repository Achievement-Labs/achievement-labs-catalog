import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import fixtures  # noqa: F401  (puts tools/ on sys.path)
import sign
import verify_signature


class SigningTests(unittest.TestCase):
    def setUp(self):
        # Ephemeral key generated in memory for this test only; never written to the repo.
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()).decode()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.manifest = self.dir / 'manifest.json'
        self.manifest.write_bytes(b'{"schema": 1}\n')
        self.public = self.dir / 'public.pem'
        self.public.write_bytes(self.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                                   serialization.PublicFormat.SubjectPublicKeyInfo))

    def tearDown(self):
        self.tmp.cleanup()

    def sign(self, pem):
        with mock.patch.dict(os.environ, {'CATALOG_SIGNING_KEY': pem}):
            return sign.main(['--manifest', str(self.manifest)])

    def verify(self):
        return verify_signature.main([str(self.public), '--manifest', str(self.manifest)])

    def test_sign_and_verify(self):
        self.assertEqual(self.sign(self.pem), 0)
        raw = base64.b64decode((self.dir / 'manifest.json.sig').read_bytes())
        self.assertEqual(len(raw), 64)  # IEEE P1363 r || s
        self.assertEqual(self.verify(), 0)

    def test_tampered_manifest_fails(self):
        self.sign(self.pem)
        self.manifest.write_bytes(b'{"schema": 2}\n')
        self.assertEqual(self.verify(), 1)

    def test_missing_or_wrong_key(self):
        self.assertEqual(self.sign(''), 2)
        other = ec.generate_private_key(ec.SECP384R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        self.assertEqual(self.sign(other), 2)
        self.assertFalse((self.dir / 'manifest.json.sig').exists())

    def test_malformed_signature(self):
        key = verify_signature.load_public_key(self.public.read_bytes())
        self.assertFalse(verify_signature.verify_bytes(key, b'x', b'not base64!'))
        self.assertFalse(verify_signature.verify_bytes(key, b'x', base64.b64encode(b'\0' * 63)))


if __name__ == '__main__':
    unittest.main()
