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

    def test_accepts_a_positioned_stl_assembly(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-stl",
                "action": "import_stl_assembly",
                "arguments": {
                    "name": "Glasses",
                    "parts": [
                        {"name": "Glasses.Frame", "path": "/tmp/frame.stl"},
                        {"name": "Glasses.LeftLeg", "path": "/tmp/left.stl"},
                    ],
                    "location": [0, -0.22, 0.22],
                    "scale": 0.002,
                },
            }
        )
        self.assertEqual(command.arguments["parts"][0]["name"], "Glasses.Frame")
        self.assertEqual(command.arguments["scale"], 0.002)

    def test_accepts_local_mesh_flattening(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-flatten",
                "action": "flatten_mesh_region",
                "arguments": {"object_name": "Glasses.LeftLeg", "bounds_min": [37, 45, 0], "bounds_max": [40, 70, 20], "axis": "X", "plane": 37.4, "direction": "positive"},
            }
        )
        self.assertEqual(command.arguments["plane"], 37.4)

    def test_accepts_mesh_patch_sealing(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-seal",
                "action": "seal_mesh_patch",
                "arguments": {"object_name": "Glasses.RightLeg", "bounds_min": [-105, 6, -10], "bounds_max": [-104, 35, 18], "axis": "X", "plane": -104.2, "depth": 0.4, "direction": "negative"},
            }
        )
        self.assertEqual(command.arguments["depth"], 0.4)

    def test_accepts_mesh_data_restoration(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-restore-mesh",
                "action": "restore_mesh_data",
                "arguments": {
                    "checkpoint_path": "/tmp/blender-codex-bridge/checkpoints/before.blend",
                    "object_names": ["Glasses.LeftLeg", "Glasses.RightLeg"],
                },
            }
        )
        self.assertEqual(command.arguments["object_names"], ["Glasses.LeftLeg", "Glasses.RightLeg"])

    def test_accepts_mesh_simplification(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-simplify",
                "action": "simplify_mesh",
                "arguments": {"object_name": "Scanned Figurine", "ratio": 0.1},
            }
        )
        self.assertEqual(command.arguments["ratio"], 0.1)
        self.assertTrue(command.arguments["cleanup"])

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

    def test_accepts_mesh_components(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-12",
                "action": "get_mesh_components",
                "arguments": {"object_name": "tmpasb4izdy.ply"},
            }
        )
        self.assertEqual(command.arguments["limit"], 12)

    def test_accepts_mouth_thickening(self):
        command = validate_command(
            {
                "protocol_version": 1,
                "request_id": "request-13",
                "action": "thicken_mouth_line",
                "arguments": {
                    "object_name": "tmpasb4izdy.ply",
                    "points": [[-0.06, -0.24, 0.1], [0.0, -0.25, 0.08], [0.06, -0.24, 0.1]],
                    "normal": [0, -1, 0],
                    "radius": 0.014,
                    "amount": 0.004,
                },
            }
        )
        self.assertEqual(command.arguments["surface_window"], 0.014)
        self.assertEqual(command.arguments["points"][0], [-0.06, -0.24, 0.1])

    def test_rejects_mouth_thickening_without_enough_points(self):
        with self.assertRaises(ProtocolError):
            validate_command(
                {
                    "protocol_version": 1,
                    "request_id": "request-14",
                    "action": "thicken_mouth_line",
                    "arguments": {
                        "object_name": "tmpasb4izdy.ply",
                        "points": [[0, -0.24, 0.08]],
                        "normal": [0, -1, 0],
                        "radius": 0.014,
                        "amount": 0.004,
                    },
                }
            )


    def _command(self, action, arguments, request_id="request-modelling"):
        return validate_command(
            {
                "protocol_version": 1,
                "request_id": request_id,
                "action": action,
                "arguments": arguments,
            }
        )

    def test_polygon_solid_defaults_orientation(self):
        command = self._command(
            "create_polygon_solid",
            {"name": "Plate", "points": [[0, 0], [10, 0], [10, 5]], "height": 6},
        )
        self.assertEqual(command.arguments["height"], 6.0)
        self.assertEqual(command.arguments["normal"], [0.0, 0.0, 1.0])
        self.assertEqual(command.arguments["up"], [0.0, 1.0, 0.0])
        self.assertEqual(command.arguments["location"], [0.0, 0.0, 0.0])

    def test_polygon_solid_rejects_short_profiles(self):
        with self.assertRaises(ProtocolError):
            self._command(
                "create_polygon_solid", {"name": "Plate", "points": [[0, 0], [10, 0]], "height": 6}
            )

    def test_polygon_solid_rejects_three_dimensional_points(self):
        with self.assertRaises(ProtocolError):
            self._command(
                "create_polygon_solid",
                {"name": "Plate", "points": [[0, 0, 0], [10, 0, 0], [10, 5, 0]], "height": 6},
            )

    def test_polygon_solid_rejects_distant_points(self):
        with self.assertRaises(ProtocolError):
            self._command(
                "create_polygon_solid",
                {"name": "Plate", "points": [[0, 0], [10, 0], [99999, 5]], "height": 6},
            )

    def test_polygon_wall_requires_thickness(self):
        with self.assertRaises(ProtocolError):
            self._command(
                "create_polygon_wall",
                {"name": "Cutter", "points": [[0, 0], [10, 0], [10, 5]], "height": 6},
            )

    def test_polygon_wall_normalizes_thickness(self):
        command = self._command(
            "create_polygon_wall",
            {
                "name": "Cutter",
                "points": [[0, 0], [10, 0], [10, 5]],
                "height": 6,
                "wall_thickness": 1.2,
            },
        )
        self.assertEqual(command.arguments["wall_thickness"], 1.2)

    def test_text_relief_defaults(self):
        command = self._command(
            "create_text_relief",
            {"name": "Label", "text": "SAMPLE", "font_path": "/tmp/f.ttf", "size": 9, "depth": 2},
        )
        self.assertEqual(command.arguments["dilate"], 0.0)
        self.assertEqual(command.arguments["tracking"], 1.0)
        self.assertEqual(command.arguments["resolution_u"], 12)

    def test_text_relief_rejects_blank_text(self):
        with self.assertRaises(ProtocolError):
            self._command(
                "create_text_relief",
                {"name": "Label", "text": "   ", "font_path": "/tmp/f.ttf", "size": 9, "depth": 2},
            )

    def test_boolean_op_rejects_identical_operands(self):
        with self.assertRaises(ProtocolError):
            self._command("boolean_op", {"target": "Plate", "tool": "Plate"})

    def test_boolean_op_rejects_unknown_operation(self):
        with self.assertRaises(ProtocolError):
            self._command("boolean_op", {"target": "Plate", "tool": "Text", "operation": "merge"})

    def test_boolean_op_lowercases_operation(self):
        command = self._command(
            "boolean_op", {"target": "Plate", "tool": "Text", "operation": "DIFFERENCE"}
        )
        self.assertEqual(command.arguments["operation"], "difference")
        self.assertTrue(command.arguments["delete_tool"])

    def test_export_mesh_refuses_path_traversal(self):
        for path in ("../escape.stl", "/etc/escape.stl", "nested/../../escape.stl"):
            with self.subTest(path=path), self.assertRaises(ProtocolError):
                self._command("export_mesh", {"objects": ["Plate"], "path": path})

    def test_export_mesh_requires_stl_suffix(self):
        with self.assertRaises(ProtocolError):
            self._command("export_mesh", {"objects": ["Plate"], "path": "part.3mf"})

    def test_export_mesh_accepts_nested_relative_path(self):
        command = self._command(
            "export_mesh", {"objects": ["Plate"], "path": "parts/cutter.stl"}
        )
        self.assertEqual(command.arguments["scale"], 1.0)
        self.assertEqual(command.arguments["format"], "stl")

    def test_check_printability_rejects_extra_arguments(self):
        with self.assertRaises(ProtocolError):
            self._command("check_printability", {"object_name": "Plate", "verbose": True})

if __name__ == "__main__":
    unittest.main()
