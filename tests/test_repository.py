from pathlib import Path
import tempfile
import unittest

from agentic_git.objects import Commit
from agentic_git.repository import AgitError, Repository


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo, created = Repository.init(self.root)
        self.assertTrue(created)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def test_add_commit_status_and_log(self) -> None:
        self.write("hello.txt", "hello\n")
        self.assertEqual(self.repo.add(["hello.txt"], self.root), ["hello.txt"])

        status = self.repo.status()
        self.assertEqual([(item.code, item.path) for item in status.staged], [("A", "hello.txt")])

        oid, commit = self.repo.commit("first commit")
        self.assertIsInstance(self.repo.read_object(oid), Commit)
        self.assertEqual(commit.message, "first commit")
        self.assertTrue(self.repo.status().clean)
        self.assertEqual([item[0] for item in self.repo.log()], [oid])

        with self.assertRaisesRegex(AgitError, "nothing staged"):
            self.repo.commit("duplicate")

    def test_diff_staged_and_unstaged(self) -> None:
        self.write("notes.txt", "one\n")
        self.repo.add(["."], self.root)
        self.repo.commit("base")

        self.write("notes.txt", "one\ntwo\n")
        unstaged = self.repo.diff()
        self.assertIn("+two", unstaged)
        self.assertEqual(self.repo.status().unstaged[0].code, "M")

        self.repo.add(["notes.txt"], self.root)
        staged = self.repo.diff(staged=True)
        self.assertIn("+two", staged)
        self.assertFalse(self.repo.status().unstaged)

    def test_deleted_file_can_be_staged(self) -> None:
        self.write("remove.txt", "temporary\n")
        self.repo.add(["."], self.root)
        self.repo.commit("base")
        (self.root / "remove.txt").unlink()

        self.repo.add(["."], self.root)
        self.assertEqual(self.repo.status().staged[0].code, "D")
        self.repo.commit("remove file")
        self.assertTrue(self.repo.status().clean)

    def test_branch_and_checkout_restore_files(self) -> None:
        self.write("value.txt", "main\n")
        self.repo.add(["."], self.root)
        main_oid, _ = self.repo.commit("main version")
        self.repo.create_branch("feature")
        self.repo.checkout("feature")

        self.write("value.txt", "feature\n")
        self.repo.add(["."], self.root)
        feature_oid, _ = self.repo.commit("feature version")
        self.assertNotEqual(feature_oid, main_oid)

        self.repo.checkout("main")
        self.assertEqual((self.root / "value.txt").read_text(), "main\n")
        self.repo.checkout("feature")
        self.assertEqual((self.root / "value.txt").read_text(), "feature\n")

    def test_checkout_protects_local_changes(self) -> None:
        self.write("value.txt", "main\n")
        self.repo.add(["."], self.root)
        self.repo.commit("base")
        self.repo.create_branch("feature")
        self.write("value.txt", "changed\n")

        with self.assertRaisesRegex(AgitError, "tracked changes"):
            self.repo.checkout("feature")

    def test_pack_with_prune_keeps_objects_readable(self) -> None:
        self.write("packed.txt", "store me\n")
        self.repo.add(["."], self.root)
        commit_oid, _ = self.repo.commit("pack test")

        result = self.repo.pack_objects(prune=True)
        self.assertGreaterEqual(result.object_count, 3)
        self.assertTrue(result.path and result.path.exists())
        self.assertIsInstance(self.repo.read_object(commit_oid), Commit)
        self.assertEqual(self.repo.log()[0][0], commit_oid)

    def test_env_file_is_ignored_for_safety(self) -> None:
        self.write(".env", "GEMINI_API_KEY=secret\n")
        self.write("safe.txt", "safe\n")
        staged = self.repo.add(["."], self.root)
        self.assertEqual(staged, ["safe.txt"])
        self.assertNotIn(".env", self.repo.status().untracked)

    def test_explicit_symbolic_link_is_rejected(self) -> None:
        self.write("real.txt", "contents\n")
        (self.root / "link.txt").symlink_to(self.root / "real.txt")
        with self.assertRaisesRegex(AgitError, "symbolic links"):
            self.repo.add(["link.txt"], self.root)


if __name__ == "__main__":
    unittest.main()
