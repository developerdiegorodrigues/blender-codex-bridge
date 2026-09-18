"""Tests for the add-on bootstrap that swaps the modeling core at runtime.

This is the most consequential code in the project: it loads a Python module
and executes it inside a running Blender. It was also the only part with no
tests, because importing it means importing bpy.

Nothing here needs Blender. The bootstrap and the core touch the Blender API
only inside functions, and no class derives from bpy.types, so empty stub
modules are enough to import them and exercise the release machinery.
"""

import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[1] / "addon"


def _stub_blender_modules() -> None:
    for name in ("bpy", "bmesh"):
        sys.modules.setdefault(name, types.ModuleType(name))
    if "mathutils" not in sys.modules:
        mathutils = types.ModuleType("mathutils")
        mathutils.Matrix = object
        mathutils.Vector = object
        sys.modules["mathutils"] = mathutils


_stub_blender_modules()
if str(ADDON) not in sys.path:
    sys.path.insert(0, str(ADDON))

import blender_codex_bridge as bootstrap  # noqa: E402  (needs the stubs above)

VALID_CORE = """
CORE_API_VERSION = 1
CORE_VERSION = "9.9.9"


def self_test():
    return {"ok": True}
"""


class BootstrapTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.runtime = Path(self._directory.name).resolve()
        self.releases = self.runtime / "releases"
        self.releases.mkdir(mode=0o700)
        self._saved_state = bootstrap.STATE
        self._saved_core = bootstrap.CORE
        bootstrap.STATE = bootstrap.BridgeState(runtime=self.runtime, token="test-token")

    def tearDown(self) -> None:
        bootstrap.STATE = self._saved_state
        bootstrap.CORE = self._saved_core
        for name in [key for key in sys.modules if key.startswith("blender_codex_bridge_core_")]:
            del sys.modules[name]
        self._directory.cleanup()

    def stage(self, body: str, name: str = "core.py", directory: Path | None = None) -> tuple[Path, str]:
        path = (directory or self.releases) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path, hashlib.sha256(path.read_bytes()).hexdigest()


class LoadCoreTests(BootstrapTestCase):
    def test_accepts_a_staged_release(self):
        path, digest = self.stage(VALID_CORE)
        module = bootstrap._load_core(path, digest)
        self.assertEqual(module.CORE_VERSION, "9.9.9")

    def test_rejects_a_release_outside_the_releases_directory(self):
        path, digest = self.stage(VALID_CORE, directory=self.runtime)
        with self.assertRaises(ValueError) as raised:
            bootstrap._load_core(path, digest)
        self.assertIn("releases directory", str(raised.exception))

    def test_rejects_a_path_that_escapes_through_a_parent_reference(self):
        path, digest = self.stage(VALID_CORE, directory=self.runtime)
        with self.assertRaises(ValueError):
            bootstrap._load_core(self.releases / ".." / path.name, digest)

    def test_rejects_a_checksum_mismatch(self):
        path, _ = self.stage(VALID_CORE)
        with self.assertRaises(ValueError) as raised:
            bootstrap._load_core(path, "0" * 64)
        self.assertIn("checksum", str(raised.exception))

    def test_rejects_a_file_that_is_not_python(self):
        path, digest = self.stage(VALID_CORE, name="core.txt")
        with self.assertRaises(ValueError):
            bootstrap._load_core(path, digest)

    def test_rejects_a_release_over_the_size_limit(self):
        body = VALID_CORE + "\n# " + "padding " * (2 * 1024 * 1024 // 8)
        path, digest = self.stage(body)
        with self.assertRaises(ValueError) as raised:
            bootstrap._load_core(path, digest)
        self.assertIn("2 MiB", str(raised.exception))

    def test_rejects_an_incompatible_core_api_version(self):
        path, digest = self.stage(VALID_CORE.replace("CORE_API_VERSION = 1", "CORE_API_VERSION = 99"))
        with self.assertRaises(RuntimeError) as raised:
            bootstrap._load_core(path, digest)
        self.assertIn("API version", str(raised.exception))

    def test_rejects_a_core_without_a_self_test(self):
        path, digest = self.stage('CORE_API_VERSION = 1\nCORE_VERSION = "9.9.9"\n')
        with self.assertRaises(RuntimeError) as raised:
            bootstrap._load_core(path, digest)
        self.assertIn("self_test", str(raised.exception))

    def test_a_failing_self_test_leaves_nothing_behind(self):
        path, digest = self.stage(VALID_CORE.replace('return {"ok": True}', 'raise RuntimeError("broken")'))
        with self.assertRaises(RuntimeError):
            bootstrap._load_core(path, digest)
        stranded = [name for name in sys.modules if name.startswith("blender_codex_bridge_core_")]
        self.assertEqual(stranded, [], "a rejected core must not stay importable")


class ReloadCoreTests(BootstrapTestCase):
    def test_swaps_the_active_core(self):
        path, digest = self.stage(VALID_CORE)
        result = bootstrap._reload_core({"release_path": str(path), "sha256": digest})
        self.assertEqual(result["core_version"], "9.9.9")
        self.assertEqual(bootstrap.CORE.CORE_VERSION, "9.9.9")
        self.assertIs(bootstrap.CORE.STATE, bootstrap.STATE)

    def test_refuses_to_swap_while_commands_are_queued(self):
        path, digest = self.stage(VALID_CORE)
        bootstrap.STATE.commands.put(bootstrap.PendingCommand(payload={}))
        with self.assertRaises(RuntimeError) as raised:
            bootstrap._reload_core({"release_path": str(path), "sha256": digest})
        self.assertIn("empty command queue", str(raised.exception))

    def test_a_rejected_release_keeps_the_running_core(self):
        running = bootstrap.CORE
        path, _ = self.stage(VALID_CORE)
        with self.assertRaises(ValueError):
            bootstrap._reload_core({"release_path": str(path), "sha256": "0" * 64})
        self.assertIs(bootstrap.CORE, running)


class ReleaseBookkeepingTests(BootstrapTestCase):
    def read_state(self) -> dict:
        return json.loads((self.runtime / "current-core.json").read_text(encoding="utf-8"))

    def test_records_the_current_release(self):
        bootstrap._write_current_release(self.releases / "a.py", "a" * 64, "1.0.0")
        state = self.read_state()
        self.assertEqual(state["current"]["sha256"], "a" * 64)
        self.assertIsNone(state["previous"])

    def test_a_new_release_becomes_current_and_demotes_the_old_one(self):
        bootstrap._write_current_release(self.releases / "a.py", "a" * 64, "1.0.0")
        bootstrap._write_current_release(self.releases / "b.py", "b" * 64, "2.0.0")
        state = self.read_state()
        self.assertEqual(state["current"]["sha256"], "b" * 64)
        self.assertEqual(state["previous"]["sha256"], "a" * 64)

    def test_redeploying_the_same_release_preserves_the_rollback_target(self):
        # Deploying an unchanged core must not overwrite previous with
        # current, which would make rollback a no-op exactly when it is
        # needed: after a bad release is re-pushed.
        bootstrap._write_current_release(self.releases / "a.py", "a" * 64, "1.0.0")
        bootstrap._write_current_release(self.releases / "b.py", "b" * 64, "2.0.0")
        bootstrap._write_current_release(self.releases / "b.py", "b" * 64, "2.0.0")
        state = self.read_state()
        self.assertEqual(state["current"]["sha256"], "b" * 64)
        self.assertEqual(state["previous"]["sha256"], "a" * 64)

    def test_survives_a_corrupt_state_file(self):
        (self.runtime / "current-core.json").write_text("{not json", encoding="utf-8")
        bootstrap._write_current_release(self.releases / "a.py", "a" * 64, "1.0.0")
        self.assertEqual(self.read_state()["current"]["core_version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
