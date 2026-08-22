import tempfile
import unittest
from pathlib import Path

from blender_bridge.runtime import RuntimePaths


class RuntimeTests(unittest.TestCase):
    def test_stop_sentinel_is_scoped_to_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = RuntimePaths(Path(directory))
            runtime.ensure()
            runtime.stop_file.touch()
            self.assertTrue(runtime.stop_file.exists())
            self.assertEqual(runtime.stop_file.parent, Path(directory))


if __name__ == "__main__":
    unittest.main()
