import unittest

from agentic_git.gemini import Action, parse_actions
from agentic_git.repository import AgitError


class GeminiActionTests(unittest.TestCase):
    def test_valid_multi_action_response(self) -> None:
        actions = parse_actions(
            '{"actions": ['
            '{"command": "add", "args": {"paths": ["."]}},'
            '{"command": "commit", "args": {"message": "first version"}}'
            "]}"
        )
        self.assertEqual(
            actions,
            [
                Action("add", {"paths": ["."]}),
                Action("commit", {"message": "first version"}),
            ],
        )

    def test_shell_commands_are_rejected(self) -> None:
        with self.assertRaisesRegex(AgitError, "unsupported"):
            parse_actions(
                '{"actions": [{"command": "shell", "args": {"value": "rm -rf ."}}]}'
            )

    def test_extra_arguments_are_rejected(self) -> None:
        with self.assertRaisesRegex(AgitError, "unexpected"):
            parse_actions(
                '{"actions": [{"command": "status", "args": {"surprise": true}}]}'
            )

    def test_non_string_command_is_rejected_cleanly(self) -> None:
        with self.assertRaisesRegex(AgitError, "unsupported"):
            parse_actions('{"actions": [{"command": [], "args": {}}]}')

    def test_more_than_five_actions_are_rejected(self) -> None:
        action = '{"command": "status", "args": {}}'
        with self.assertRaisesRegex(AgitError, "number"):
            parse_actions('{"actions": [' + ",".join([action] * 6) + "]}")


if __name__ == "__main__":
    unittest.main()
