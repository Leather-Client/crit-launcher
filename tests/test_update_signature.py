# Tests for the launcher update signature check.
# Language: Python 3.9+. Run with:  python3 -m unittest discover -s tests
#
# Points CRIT_LAUNCHER_DATA at a temp dir before importing launcher, because the
# module creates its data layout at import time. pywebview is only imported
# inside main(), so nothing here needs a GUI stack.
import base64
import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_temp_data = tempfile.mkdtemp(prefix="crit-update-test-")
os.environ["CRIT_LAUNCHER_DATA"] = _temp_data

import launcher  # noqa: E402


# --- a minimal ed25519 signer, so tests don't depend on openssl being installed
_ED_P = launcher._ED_P
_ED_Q = launcher._ED_Q
_ED_D = launcher._ED_D
_ED_G = launcher._ED_G


def _ed_encode(point):
    y = point[1] * pow(point[2], _ED_P - 2, _ED_P) % _ED_P
    x = point[0] * pow(point[2], _ED_P - 2, _ED_P) % _ED_P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _ed_sign(seed: bytes, message: bytes):
    """Small reference signer: enough for the test, not for production use."""
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    public = _ed_encode(launcher._ed_mul(a, _ED_G))
    r = int.from_bytes(hashlib.sha512(h[32:] + message).digest(), "little") % _ED_Q
    R = _ed_encode(launcher._ed_mul(r, _ED_G))
    k = int.from_bytes(hashlib.sha512(R + public + message).digest(), "little") % _ED_Q
    s = (r + k * a) % _ED_Q
    return public, R + int.to_bytes(s, 32, "little")


class SignedReleaseTestCase(unittest.TestCase):
    """Builds a fake GitHub release the updater can be pointed at."""

    ASSET = "setup.exe"
    BODY = b"installer-bytes"

    def setUp(self):
        self.seed = hashlib.sha256(b"crit-test-key").digest()
        self.public, _ = _ed_sign(self.seed, b"")
        self.key_b64 = base64.b64encode(self.public).decode()

        self.sums = ("%s  %s\n" % (hashlib.sha256(self.BODY).hexdigest(), self.ASSET)).encode()
        self.tag = "v1.8"
        message = ("crit launcher release %s\n" % self.tag).encode() + self.sums
        _, signature = _ed_sign(self.seed, message)
        self.sig_b64 = (base64.b64encode(signature).decode() + "\n").encode()

        self.assets = {
            launcher.UPDATE_SUMS: {"browser_download_url": "https://x/SHA256SUMS"},
            launcher.UPDATE_SIG: {"browser_download_url": "https://x/SHA256SUMS.sig"},
            self.ASSET: {"browser_download_url": "https://x/" + self.ASSET},
        }
        self.release = {"tag_name": self.tag, "assets": []}

        self._patch = mock.patch.object(launcher, "http_get_bytes", self._fetch)
        self._patch.start()
        self.addCleanup(self._patch.stop)

        self._keys = mock.patch.object(launcher, "UPDATE_TRUSTED_KEYS", (self.key_b64,))
        self._keys.start()
        self.addCleanup(self._keys.stop)

    def _fetch(self, url, limit=None, timeout=60):
        if url.endswith(launcher.UPDATE_SUMS):
            return self.sums
        if url.endswith(launcher.UPDATE_SIG):
            return self.sig_b64
        raise AssertionError("unexpected fetch " + url)

    def _release(self, **overrides):
        release = {"tag_name": self.tag, "assets": [
            {"name": k, **v} for k, v in self.assets.items()]}
        release.update(overrides)
        return release

    # --- the happy path -----------------------------------------------------

    def test_good_signature_returns_signed_hash(self):
        got = launcher.update_signed_sha256(self._release(), self.ASSET)
        self.assertEqual(got, hashlib.sha256(self.BODY).hexdigest())

    def test_asset_name_with_star_prefix(self):
        self.sums = self.sums.replace(b"  setup.exe", b" *setup.exe")
        message = ("crit launcher release %s\n" % self.tag).encode() + self.sums
        _, sig = _ed_sign(self.seed, message)
        self.sig_b64 = base64.b64encode(sig).decode().encode()
        self.assertEqual(launcher.update_signed_sha256(self._release(), self.ASSET),
                         hashlib.sha256(self.BODY).hexdigest())

    # --- tampering ----------------------------------------------------------

    def test_unsigned_release_is_refused(self):
        release = self._release()
        release["assets"] = [a for a in release["assets"]
                             if a["name"] not in (launcher.UPDATE_SUMS, launcher.UPDATE_SIG)]
        with self.assertRaises(RuntimeError):
            launcher.update_signed_sha256(release, self.ASSET)

    def test_tampered_sums_are_refused(self):
        # attacker swaps the hash but keeps the old signature
        self.sums = ("%s  %s\n" % ("0" * 64, self.ASSET)).encode()
        with self.assertRaises(RuntimeError):
            launcher.update_signed_sha256(self._release(), self.ASSET)

    def test_wrong_key_is_refused(self):
        other_seed = hashlib.sha256(b"someone-else").digest()
        other_public, _ = _ed_sign(other_seed, b"")
        with mock.patch.object(launcher, "UPDATE_TRUSTED_KEYS",
                               (base64.b64encode(other_public).decode(),)):
            with self.assertRaises(RuntimeError):
                launcher.update_signed_sha256(self._release(), self.ASSET)

    def test_signature_for_different_release_is_refused(self):
        # replay a valid signature from another tag
        self.tag = "v1.7"
        message = ("crit launcher release v1.8\n").encode() + self.sums
        _, sig = _ed_sign(self.seed, message)
        self.sig_b64 = base64.b64encode(sig).decode().encode()
        with self.assertRaises(RuntimeError):
            launcher.update_signed_sha256(self._release(), self.ASSET)

    def test_asset_missing_from_signed_sums_is_refused(self):
        with self.assertRaises(RuntimeError):
            launcher.update_signed_sha256(self._release(), "other.exe")

    def test_garbage_signature_lines_are_skipped(self):
        self.sig_b64 = b"not base64 at all\n" + self.sig_b64
        self.assertEqual(launcher.update_signed_sha256(self._release(), self.ASSET),
                         hashlib.sha256(self.BODY).hexdigest())


class TestNothingRunsWhenUnverified(unittest.TestCase):
    """The worker must not reach os.startfile when verification fails."""

    def _run_worker(self, keys, release):
        api = launcher.API.__new__(launcher.API)
        api._window = None
        emitted = []
        api.emit = lambda name, data=None: emitted.append((name, data))

        with mock.patch.object(launcher.API, "get_changelog",
                               lambda self, f=False: release), \
                mock.patch.object(launcher, "UPDATE_TRUSTED_KEYS", keys), \
                mock.patch.object(launcher, "http_download") as download, \
                mock.patch.object(launcher.os, "startfile", create=True) as startfile:
            launcher.API._update_worker(api)
        return emitted, download, startfile

    def test_unsigned_release_never_downloads_or_runs(self):
        fake = {"success": True, "setup_url": "https://x/setup.exe", "setup_size": 10,
                "assets": [], "release_tag": "v9"}
        emitted, download, startfile = self._run_worker(("AAAA",), fake)
        download.assert_not_called()
        startfile.assert_not_called()
        self.assertIn("updateError", [e[0] for e in emitted])

    def test_bad_signature_never_downloads_or_runs(self):
        bad = {"success": True, "setup_url": "https://x/setup.exe", "setup_size": 10,
               "release_tag": "v9", "assets": [
                   {"name": launcher.UPDATE_SUMS, "browser_download_url": "https://x/SHA256SUMS"},
                   {"name": launcher.UPDATE_SIG, "browser_download_url": "https://x/SHA256SUMS.sig"}]}
        sums = ("%s  setup.exe\n" % ("0" * 64)).encode()
        key = base64.b64encode(hashlib.sha256(b"k").digest()).decode()
        with mock.patch.object(launcher, "http_get_bytes",
                               lambda url, limit=None, timeout=60:
                               sums if url.endswith("SUM" + "S") else b"A" * 88):
            emitted, download, startfile = self._run_worker((key,), bad)
        download.assert_not_called()
        startfile.assert_not_called()
        self.assertIn("updateError", [e[0] for e in emitted])

    def test_empty_keys_still_updates_for_existing_installs(self):
        # No key configured yet: 1.7 must keep updating, just without verification.
        fake = {"success": True, "setup_url": "https://x/setup.exe", "setup_size": 10,
                "assets": [], "release_tag": "v9"}
        emitted, download, startfile = self._run_worker((), fake)
        download.assert_called_once()
        self.assertNotIn("updateError", [e[0] for e in emitted])


if __name__ == "__main__":
    unittest.main()
