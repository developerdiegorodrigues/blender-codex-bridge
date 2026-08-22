import unittest

from blender_bridge.protocol import ProtocolError, validate_command


class ProtocolTests(unittest.TestCase):
    def test_accepts_scene_summary(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-1",
                "action": "get_scene_summary",
                "arguments": {},
            }
        )
        self.assertEqual(command.action, "get_scene_summary")

    def test_normalizes_transform_vectors(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-2",
                "action": "transform_object",
                "arguments": {"name": "Cube", "location": [1, 2, 3]},
            }
        )
        self.assertEqual(command.arguments["location"], [1.0, 2.0, 3.0])

    def test_rejects_unknown_actions(self):
        with self.assertRaises(ProtocolError):
            validate_command(
                {
                    "protocol_version": 1,
                    "request_id": "request-3",
                    "action": "execute_python",
                    "arguments": {},
                }
            )

    def test_transform_requires_a_vector(self):
        with self.assertRaises(ProtocolError):
            validate_command(
                {
                    "protocol_version": 1,
                    "request_id": "request-4",
                    "action": "transform_object",
                    "arguments": {"name": "Cube"},
                }
            )

    def test_accepts_a_closed_hemisphere(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-5",
                "action": "create_hemisphere",
                "arguments": {
                    "name": "Pupil.L",
                    "location": [0.075, -0.205, 0.22],
                    "radius": 0.012,
                    "direction": "-Y",
                },
            }
        )
        self.assertEqual(command.arguments["depth"], 0.012)
        self.assertEqual(command.arguments["segments"], 48)

    def test_rejects_an_invalid_hemisphere_direction(self):
        with self.assertRaises(ProtocolError):
            validate_command(
                {
                    "protocol_version": 1,
                    "request_id": "request-6",
                    "action": "create_hemisphere",
                    "arguments": {
                        "name": "Pupil.L",
                        "location": [0, 0, 0],
                        "radius": 0.012,
                        "direction": "FRONT",
                    },
                }
            )

    def test_accepts_an_image_relief(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-7",
                "action": "create_image_relief",
                "arguments": {
                    "name": "Back.Symbol",
                    "image_path": "/tmp/symbol.png",
                    "location": [0, 0.12, -0.1],
                    "normal": [0, 1, 0.16],
                    "up": [0, -0.16, 1],
                    "width": 0.09,
                    "depth": 0.006,
                },
            }
        )
        self.assertEqual(command.arguments["resolution"], 192)
        self.assertEqual(command.arguments["threshold"], 0.5)

    def test_rejects_an_oversized_relief_resolution(self):
        with self.assertRaises(ProtocolError):
            validate_command(
                {
                    "protocol_version": 1,
                    "request_id": "request-8",
                    "action": "create_image_relief",
                    "arguments": {
                        "name": "Back.Symbol",
                        "image_path": "/tmp/symbol.png",
                        "location": [0, 0, 0],
                        "normal": [0, 1, 0],
                        "up": [0, 0, 1],
                        "width": 0.09,
                        "depth": 0.006,
                        "resolution": 4096,
                    },
                }
            )

    def test_accepts_a_core_reload(self):
        digest = "a" * 64
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-9",
                "action": "reload_core",
                "arguments": {
                    "release_path": "/tmp/blender-codex-bridge/releases/a/core.py",
                    "sha256": digest,
                },
            }
        )
        self.assertEqual(command.arguments["sha256"], digest)

    def test_rejects_an_invalid_core_checksum(self):
        with self.assertRaises(ProtocolError):
            validate_command(
                {
                    "protocol_version": 1,
                    "request_id": "request-10",
                    "action": "reload_core",
                    "arguments": {"release_path": "/tmp/core.py", "sha256": "not-a-digest"},
                }
            )

    def test_accepts_a_back_preview(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-11",
                "action": "render_workbench_preview",
                "arguments": {"view": "back", "target": [0, 0, 0.04], "ortho_scale": 1.0},
            }
        )
        self.assertEqual(command.arguments["view"], "back")
        self.assertEqual(command.arguments["resolution"], 800)


if __name__ == "__main__":
    unittest.main()
