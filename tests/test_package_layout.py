# Guards the package layout. Run from the project root:  python3 -m unittest discover -s tests
import os
import pkgutil
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_data = tempfile.mkdtemp(prefix="crit-layout-test-")
os.environ["CRIT_LAUNCHER_DATA"] = _data
sys.path.insert(0, str(ROOT))

import crit_launcher  # noqa: E402
from crit_launcher.app.facade import API  # noqa: E402


def public_api():
    return sorted(n for n in dir(API) if not n.startswith("_") and callable(getattr(API, n)))


class LayoutTests(unittest.TestCase):
    def test_every_module_imports(self):
        import importlib
        for mod in pkgutil.walk_packages(crit_launcher.__path__, "crit_launcher."):
            importlib.import_module(mod.name)

    def test_import_has_no_side_effects(self):
        fresh = Path(tempfile.mkdtemp(prefix="crit-noside-")) / "data"
        code = "import crit_launcher.app.facade"
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True,
                       env={**os.environ, "CRIT_LAUNCHER_DATA": str(fresh)})
        self.assertFalse(fresh.exists(), "importing the launcher created the data folder")

    def test_api_surface_unchanged(self):
        expected = (ROOT / "tests/api_surface.txt").read_text().split()
        self.assertEqual(public_api(), sorted(expected))

    def test_pages_only_call_existing_methods(self):
        have = set(public_api())
        for page in ("index.html", "index.linux.html"):
            text = (ROOT / page).read_text(encoding="utf-8")
            used = set(re.findall(r'callApi\(\s*["\']([A-Za-z_0-9]+)["\']', text))
            used |= set(re.findall(r"pywebview\.api\.([A-Za-z_0-9]+)", text))
            self.assertEqual(sorted(used - have), [], page)

    def test_domain_modules_do_not_import_api_modules(self):
        for path in (ROOT / "crit_launcher").rglob("*.py"):
            if path.name.startswith("api") or path.name == "facade.py":
                continue
            for line in path.read_text().splitlines():
                m = re.match(r"from \.[\w.]* import", line)
                if m and re.search(r"\bapi\w*\b", line.split(" import")[0]):
                    self.fail("%s imports an api module: %s" % (path.relative_to(ROOT), line))

    def test_version_line_format(self):
        text = (ROOT / "crit_launcher/version.py").read_text()
        self.assertRegex(text, r'(?m)^APP_VERSION = "\d+\.\d+(\.\d+)?"$')


if __name__ == "__main__":
    unittest.main()
