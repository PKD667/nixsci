import os
import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from nixsci.lab import home

DIRS = st.lists(st.from_regex(r"[a-z]{1,6}", fullmatch=True), max_size=4)


class Project(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.pop("NIXSCI_STORE", None)
        self.addCleanup(lambda: os.environ.__setitem__("NIXSCI_STORE", self.old) if self.old else None)

    @given(DIRS)
    def test_a_spec_anywhere_in_a_git_checkout_uses_the_store_at_its_root(self, below):
        root = Path(tempfile.mkdtemp()) / "proj"
        (root / ".git").mkdir(parents=True)
        spec = root.joinpath(*below, "e.toml")
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("")
        self.assertEqual(home.runs_dir(spec), root.resolve() / ".nixsci" / "runs")
        self.assertEqual((root / ".nixsci" / ".gitignore").read_text(), "*\n")

    def test_without_git_the_spec_directory_is_the_project_and_the_environment_overrides(self):
        spec = Path(tempfile.mkdtemp()) / "loose" / "e.toml"
        spec.parent.mkdir()
        spec.write_text("")
        self.assertEqual(home.store_root(spec), spec.parent.resolve() / ".nixsci")
        os.environ["NIXSCI_STORE"] = "/elsewhere"
        self.addCleanup(os.environ.pop, "NIXSCI_STORE")
        self.assertEqual(home.store_root(spec), Path("/elsewhere"))


if __name__ == "__main__":
    unittest.main()
