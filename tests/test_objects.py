import unittest

from agentic_git.objects import (
    Blob,
    Commit,
    ObjectFormatError,
    Tree,
    TreeEntry,
    decode_object,
    encode_object,
    object_id,
)


class ObjectTests(unittest.TestCase):
    def test_blob_uses_git_style_sha1_header(self) -> None:
        blob = Blob(b"hello")
        self.assertEqual(
            object_id(blob),
            "b6fc4c620b67d95f953a5c1c1230aaab5db5a1b0",
        )
        self.assertEqual(decode_object(encode_object(blob)), blob)

    def test_tree_serialization_is_sorted_and_round_trips(self) -> None:
        first = "1" * 40
        second = "2" * 40
        tree = Tree((TreeEntry("z.txt", second), TreeEntry("a.txt", first)))
        decoded = decode_object(encode_object(tree))
        self.assertIsInstance(decoded, Tree)
        self.assertEqual([entry.path for entry in decoded.entries], ["a.txt", "z.txt"])

    def test_commit_round_trips(self) -> None:
        commit = Commit(
            tree="a" * 40,
            parent=None,
            message="first commit",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(decode_object(encode_object(commit)), commit)

    def test_bad_size_is_rejected(self) -> None:
        with self.assertRaises(ObjectFormatError):
            decode_object(b"blob 10\0short")


if __name__ == "__main__":
    unittest.main()

