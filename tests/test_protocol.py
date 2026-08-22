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


if __name__ == "__main__":
    unittest.main()
