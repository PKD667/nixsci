import contextlib
import io
import unittest

from nixsci import cli


class Dispatch(unittest.TestCase):
    def run_cli(self, argv, commands):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv, commands)
        return code, out.getvalue(), err.getvalue()

    def test_the_rest_of_the_line_goes_to_the_named_command(self):
        seen = []
        code, _, _ = self.run_cli(["deploy", "lease", "ls"], {"deploy": lambda argv: seen.append(argv) or 3})
        self.assertEqual((code, seen), (3, [["lease", "ls"]]))

    def test_a_command_that_returns_nothing_succeeds(self):
        self.assertEqual(self.run_cli(["lab"], {"lab": lambda argv: None})[0], 0)

    def test_an_unknown_command_names_the_known_ones(self):
        code, _, err = self.run_cli(["nope"], {"deploy": print, "lab": print})
        self.assertEqual(code, 2)
        self.assertIn("deploy, lab", err)

    def test_help_lists_commands_and_no_arguments_is_an_error(self):
        code, out, _ = self.run_cli(["--help"], {"lab": print})
        self.assertEqual((code, "lab" in out), (0, True))
        self.assertEqual(self.run_cli([], {"lab": print})[0], 2)


if __name__ == "__main__":
    unittest.main()
