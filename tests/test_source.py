import subprocess
import tempfile
import unittest
from pathlib import Path

from nix_deploy import nix


class SourceIdentity(unittest.TestCase):
    def test_origin_records_the_git_revision_not_just_the_store_snapshot(self):
        root = Path(tempfile.mkdtemp())
        (root / "flake.nix").write_text("{ outputs = { self }: { x = 1; }; }\n")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(root)]
        subprocess.run([*git[:5], "init", "-q", str(root)], check=True)
        subprocess.run([*git, "add", "flake.nix"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
        rev = subprocess.run(
            [*git, "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        source, snapshot = nix.capture_source("nix", str(root))
        self.assertEqual(source["origin"]["rev"], rev)
        self.assertEqual(source["origin"]["type"], "git")
        self.assertEqual(source["locked"]["type"], "path")  # the snapshot, as before
        self.assertTrue(snapshot.startswith("/nix/store/"))


if __name__ == "__main__":
    unittest.main()
