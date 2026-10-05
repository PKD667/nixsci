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

    def test_an_unknown_command_names_the_known_ones(self):
        code, _, err = self.run_cli(["nope"], {"deploy": print, "lab": print})
        self.assertEqual(code, 2)
        self.assertIn("deploy, lab", err)



if __name__ == "__main__":
    unittest.main()
