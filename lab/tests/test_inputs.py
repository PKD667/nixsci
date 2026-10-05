import hashlib
import tempfile
import unittest
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from nixsci.lab.inputs import Ambiguous, Missing, Store

CONTENT = st.binary(min_size=1, max_size=200)


def fresh():
    root = Path(tempfile.mkdtemp())
    return Store(root / "store"), root


def blob(store, root, data, name="model.bin"):
    path = root / name
    path.write_bytes(data)
    return store.put_blob(path)


class Blobs(unittest.TestCase):
    @given(CONTENT)
    def test_a_blob_is_named_by_the_hash_of_its_bytes_and_stored_once(self, data):
        store, root = fresh()
        first, second = blob(store, root, data), blob(store, root, data)
        self.assertEqual(first, second)
        self.assertEqual(first["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(store.blob_path(first["sha256"]).read_bytes(), data)
        self.assertEqual(len(list((store.root / "blobs").iterdir())), 1)


class Lineage(unittest.TestCase):
    @settings(deadline=None)
    @given(st.lists(CONTENT, min_size=1, max_size=5, unique=True))
    def test_each_revision_extends_the_last_and_is_numbered_by_its_depth(self, versions):
        store, root = fresh()
        hashes = [store.add("model", "m", [blob(store, root, data)])[0] for data in versions]
        self.assertEqual([store.manifest(h)["number"] for h in hashes], list(range(1, len(versions) + 1)))
        self.assertEqual(store.resolve("m"), hashes[-1])
        for number, sha in enumerate(hashes, start=1):
            self.assertEqual(store.resolve(f"m@{number}"), sha)
            self.assertEqual(store.resolve(f"model:m#{sha[:8]}"), sha)

    @settings(deadline=None)
    @given(CONTENT)
    def test_importing_what_the_tip_already_holds_changes_nothing(self, data):
        store, root = fresh()
        sha, new = store.add("model", "m", [blob(store, root, data)])
        again, new_again = store.add("model", "m", [blob(store, root, data)])
        self.assertEqual((again, new, new_again), (sha, True, False))

    @settings(deadline=None)
    @given(st.lists(CONTENT, min_size=3, max_size=3, unique=True))
    def test_two_children_of_one_parent_are_a_fork_that_must_be_pinned(self, contents):
        store, root = fresh()
        base, _ = store.add("model", "m", [blob(store, root, contents[0])])
        left, _ = store.add("model", "m", [blob(store, root, contents[1])], parent=base)
        right, _ = store.add("model", "m", [blob(store, root, contents[2])], parent=base)
        self.assertEqual(store.manifest(left)["number"], store.manifest(right)["number"])
        for ref in ("m", "m@2"):
            with self.assertRaises(Ambiguous):
                store.resolve(ref)
        self.assertEqual(store.resolve(f"m#{left}"), left)
        with self.assertRaises(Ambiguous):
            store.add("model", "m", [blob(store, root, b"x" + contents[0])])


class Integrity(unittest.TestCase):
    def test_a_modified_manifest_or_blob_is_reported_not_trusted(self):
        store, root = fresh()
        sha, _ = store.add("model", "m", [blob(store, root, b"weights")])
        self.assertEqual(store.verify(sha), [])
        store.blob_path(store.manifest(sha)["blobs"][0]["sha256"]).write_bytes(b"tampered")
        self.assertEqual(store.verify(sha), [("model.bin", "changed")])
        (store.root / "manifests" / f"{sha}.json").write_text("{}")
        with self.assertRaises(ValueError):
            store.manifest(sha)

    def test_names_keep_one_kind_and_unknown_references_are_missing(self):
        store, root = fresh()
        store.add("model", "m", [blob(store, root, b"w")])
        with self.assertRaises(ValueError):
            store.add("dataset", "m", [blob(store, root, b"other")])
        with self.assertRaises(Missing):
            store.resolve("dataset:m")
        with self.assertRaises(Missing):
            store.resolve("nothing")


class Datasets(unittest.TestCase):
    def table(self, root, rows):
        import pyarrow as pa
        import pyarrow.parquet as pq

        path = root / "t.parquet"
        pq.write_table(pa.table({"time": [float(i) for i in range(rows)], "unit": list(range(rows)), "split": ["train"] * rows}), path)
        return path

    def test_a_table_is_described_from_its_own_schema_and_the_gaps_are_listed(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow not installed")
        store, root = fresh()
        sha, _ = store.import_dataset("shd", self.table(root, 3), units={"time": "s"})
        body = store.manifest(sha)
        self.assertEqual((body["details"]["rows"], body["details"]["units"]), (3, {"time": "s"}))
        self.assertEqual(body["details"]["columns"], {"time": "double", "unit": "int64", "split": "string"})
        self.assertEqual(body["gaps"], ["unit of unit"])
        grown, _ = store.import_dataset("shd", self.table(root, 5), units={"time": "s"})
        self.assertEqual((store.resolve("shd"), store.manifest(grown)["number"]), (grown, 2))
        self.assertEqual(store.diff(sha, grown)["rows"], [3, 5])

    def test_units_for_a_missing_column_are_refused(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow not installed")
        store, root = fresh()
        with self.assertRaises(ValueError):
            store.import_dataset("shd", self.table(root, 2), units={"nope": "s"})


if __name__ == "__main__":
    unittest.main()
