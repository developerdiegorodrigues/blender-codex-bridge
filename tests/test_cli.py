import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from blender_bridge import cli
from blender_bridge.protocol import ALLOWED_ACTIONS


class ParserTests(unittest.TestCase):
    def test_transform_accepts_a_single_vector(self):
        args = cli.build_parser().parse_args(["transform", "--name", "Cube", "--location", "1", "2", "3"])
        self.assertEqual(args.name, "Cube")
        self.assertEqual(args.location, [1.0, 2.0, 3.0])
        self.assertIsNone(args.rotation)
        self.assertIsNone(args.scale)

    def test_transform_accepts_every_vector(self):
        args = cli.build_parser().parse_args(
            [
                "transform",
                "--name",
                "Cube",
                "--location",
                "0",
                "0",
                "1",
                "--rotation",
                "0",
                "0",
                "0",
                "--scale",
                "2",
                "2",
                "2",
            ]
        )
        self.assertEqual(args.scale, [2.0, 2.0, 2.0])

    def test_undo_takes_no_arguments(self):
        args = cli.build_parser().parse_args(["undo"])
        self.assertEqual(args.command, "undo")

    def test_transform_without_a_vector_fails_before_the_network(self):
        # validate_command owns the rule, so the client rejects the call
        # locally instead of sending an unusable command to Blender.
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = cli.main(["transform", "--name", "Cube"])
        self.assertEqual(status, 1)
        self.assertIn("location, rotation, or scale", stderr.getvalue())


class ReachabilityTests(unittest.TestCase):
    """Every action the protocol accepts must be reachable from the client.

    This reads cli.py as text rather than driving it, because mapping a
    subcommand back to the action it sends would otherwise need a live
    bridge. It exists because transform_object and undo were implemented in
    the core and accepted by the protocol while no subcommand could send
    them, which left the phase one acceptance criteria impossible to run.
    """

    def test_no_action_is_stranded(self):
        source = Path(cli.__file__).read_text(encoding="utf-8")
        stranded = sorted(action for action in ALLOWED_ACTIONS if f'"{action}"' not in source)
        self.assertEqual(stranded, [], f"actions with no way to reach them from the CLI: {stranded}")


if __name__ == "__main__":
    unittest.main()
