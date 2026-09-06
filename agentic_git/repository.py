"""Repository operations for the small `.agit` file format."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import zlib

from .objects import (
    Blob,
    Commit,
    StoredObject,
    Tree,
    TreeEntry,
    decode_object,
    encode_object,
    object_id,
)


REPO_DIR = ".agit"
PACK_MAGIC = b"AGITPACK1\n"
IGNORED_DIRS = {REPO_DIR, ".git", ".venv", "__pycache__"}
IGNORED_FILES = {".env", ".DS_Store"}


class AgitError(RuntimeError):
    """A friendly error that can be shown directly by the CLI."""


@dataclass(frozen=True)
class Change:
    code: str
    path: str


@dataclass(frozen=True)
class Status:
    staged: tuple[Change, ...]
    unstaged: tuple[Change, ...]
    untracked: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not (self.staged or self.unstaged or self.untracked)


@dataclass(frozen=True)
class PackResult:
    path: Path | None
    object_count: int
    pruned: bool


class Repository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.gitdir = self.root / REPO_DIR

    @classmethod
    def init(cls, path: Path | str = ".") -> tuple["Repository", bool]:
        root = Path(path).resolve()
        root.mkdir(parents=True, exist_ok=True)
        repo = cls(root)
        created = not repo.gitdir.exists()
        (repo.gitdir / "objects").mkdir(parents=True, exist_ok=True)
        (repo.gitdir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
        (repo.gitdir / "packs").mkdir(parents=True, exist_ok=True)
        if not (repo.gitdir / "HEAD").exists():
            repo._write_text(repo.gitdir / "HEAD", "ref: refs/heads/main\n")
        if not (repo.gitdir / "index.json").exists():
            repo._write_json(repo.gitdir / "index.json", {"files": {}})
        return repo, created

    @classmethod
    def discover(cls, start: Path | str = ".") -> "Repository":
        current = Path(start).resolve()
        if current.is_file():
            current = current.parent
        for candidate in (current, *current.parents):
            if (candidate / REPO_DIR).is_dir():
                return cls(candidate)
        raise AgitError("not inside an Agentic Git repository (run 'agit init')")

    @property
    def current_branch(self) -> str:
        head = self._read_text(self.gitdir / "HEAD").strip()
        prefix = "ref: refs/heads/"
        if not head.startswith(prefix):
            raise AgitError("HEAD is malformed")
        return head[len(prefix) :]

    def head_oid(self) -> str | None:
        ref_path = self.gitdir / "refs" / "heads" / self.current_branch
        if not ref_path.exists():
            return None
        value = self._read_text(ref_path).strip()
        if not _is_oid(value):
            raise AgitError(f"branch '{self.current_branch}' contains an invalid commit id")
        return value

    def store_object(self, obj: StoredObject) -> str:
        oid = object_id(obj)
        path = self._loose_path(oid)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(zlib.compress(encode_object(obj)))
        return oid

    def read_object(self, oid: str) -> StoredObject:
        if not _is_oid(oid):
            raise AgitError(f"invalid object id: {oid}")

        loose = self._loose_path(oid)
        try:
            compressed = loose.read_bytes() if loose.exists() else self._read_packed(oid)
            raw = zlib.decompress(compressed)
            obj = decode_object(raw)
        except (OSError, zlib.error, ValueError) as exc:
            raise AgitError(f"cannot read object {oid}: {exc}") from exc

        if object_id(obj) != oid:
            raise AgitError(f"object {oid} failed its SHA-1 integrity check")
        return obj

    def add(self, paths: list[str], cwd: Path | str = ".") -> list[str]:
        if not paths:
            raise AgitError("add requires at least one path")

        index = self.load_index()
        cwd_path = Path(cwd).resolve()
        changed: set[str] = set()

        for user_path in paths:
            candidate = cwd_path / user_path
            if candidate.is_symlink():
                raise AgitError(f"symbolic links are not supported: {user_path}")
            target = candidate.resolve()
            self._ensure_inside_root(target)
            relative = self._relative(target)

            if target.is_file():
                self._ensure_not_ignored(relative)
                self._stage_file(target, relative, index)
                changed.add(relative)
                continue

            if target.is_dir():
                found = {
                    self._relative(file_path): file_path
                    for file_path in self._walk_files(target)
                }
                prefix = "" if target == self.root else relative.rstrip("/") + "/"
                for tracked in list(index):
                    if (not prefix or tracked.startswith(prefix)) and tracked not in found:
                        del index[tracked]
                        changed.add(tracked)
                for rel_path, file_path in found.items():
                    self._stage_file(file_path, rel_path, index)
                    changed.add(rel_path)
                continue

            if relative in index:
                del index[relative]
                changed.add(relative)
                continue
            raise AgitError(f"path does not exist: {user_path}")

        self.save_index(index)
        return sorted(changed)

    def load_index(self) -> dict[str, str]:
        try:
            value = json.loads(self._read_text(self.gitdir / "index.json"))
            files = value["files"]
            if not isinstance(files, dict):
                raise TypeError
            result = {str(path): str(oid) for path, oid in files.items()}
            if any(not _is_oid(oid) for oid in result.values()):
                raise TypeError
            return result
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise AgitError("the staging index is malformed") from exc

    def save_index(self, files: dict[str, str]) -> None:
        self._write_json(self.gitdir / "index.json", {"files": dict(sorted(files.items()))})

    def commit(self, message: str) -> tuple[str, Commit]:
        message = message.strip()
        if not message:
            raise AgitError("commit message cannot be empty")

        index = self.load_index()
        tree = Tree(tuple(TreeEntry(path, oid) for path, oid in sorted(index.items())))
        tree_oid = object_id(tree)
        parent = self.head_oid()
        if parent:
            parent_commit = self._expect_commit(parent)
            if parent_commit.tree == tree_oid:
                raise AgitError("nothing staged to commit")

        self.store_object(tree)
        commit = Commit(
            tree=tree_oid,
            parent=parent,
            message=message,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        commit_oid = self.store_object(commit)
        ref_path = self.gitdir / "refs" / "heads" / self.current_branch
        self._write_text(ref_path, commit_oid + "\n")
        return commit_oid, commit

    def log(self, limit: int | None = None) -> list[tuple[str, Commit]]:
        result: list[tuple[str, Commit]] = []
        oid = self.head_oid()
        visited: set[str] = set()
        while oid and (limit is None or len(result) < limit):
            if oid in visited:
                raise AgitError("commit history contains a cycle")
            visited.add(oid)
            commit = self._expect_commit(oid)
            result.append((oid, commit))
            oid = commit.parent
        return result

    def status(self) -> Status:
        index = self.load_index()
        head = self._head_files()
        staged = tuple(self._compare_maps(head, index))

        unstaged: list[Change] = []
        for path, indexed_oid in sorted(index.items()):
            full_path = self.root / path
            if not full_path.is_file():
                unstaged.append(Change("D", path))
                continue
            working_oid = object_id(Blob(full_path.read_bytes()))
            if working_oid != indexed_oid:
                unstaged.append(Change("M", path))

        tracked = set(index)
        untracked = tuple(
            sorted(
                self._relative(path)
                for path in self._walk_files(self.root)
                if self._relative(path) not in tracked
            )
        )
        return Status(staged=staged, unstaged=tuple(unstaged), untracked=untracked)

    def diff(self, staged: bool = False) -> str:
        index = self.load_index()
        if staged:
            old_files = self._head_files()
            new_files: dict[str, str | None] = index
        else:
            old_files = index
            new_files = {
                path: object_id(Blob((self.root / path).read_bytes()))
                if (self.root / path).is_file()
                else None
                for path in index
            }

        output: list[str] = []
        for path in sorted(set(old_files) | set(new_files)):
            old_oid = old_files.get(path)
            new_oid = new_files.get(path)
            if old_oid == new_oid:
                continue
            old_data = self._blob_bytes(old_oid) if old_oid else b""
            if staged:
                new_data = self._blob_bytes(new_oid) if new_oid else b""
            else:
                full_path = self.root / path
                new_data = full_path.read_bytes() if full_path.is_file() else b""
            output.append(self._unified_diff(path, old_data, new_data))
        return "".join(output)

    def branches(self) -> list[str]:
        heads = self.gitdir / "refs" / "heads"
        names = [path.relative_to(heads).as_posix() for path in heads.rglob("*") if path.is_file()]
        if self.current_branch not in names:
            names.append(self.current_branch)
        return sorted(names)

    def create_branch(self, name: str) -> str:
        self._validate_branch_name(name)
        oid = self.head_oid()
        if oid is None:
            raise AgitError("make the first commit before creating a branch")
        path = self.gitdir / "refs" / "heads" / name
        if path.exists():
            raise AgitError(f"branch already exists: {name}")
        self._write_text(path, oid + "\n")
        return oid

    def checkout(self, name: str) -> str:
        self._validate_branch_name(name)
        ref_path = self.gitdir / "refs" / "heads" / name
        if not ref_path.is_file():
            raise AgitError(f"unknown branch: {name}")
        if name == self.current_branch:
            return name

        state = self.status()
        if state.staged or state.unstaged:
            raise AgitError("commit or restore tracked changes before checkout")

        target_oid = self._read_text(ref_path).strip()
        target_files = self._files_for_commit(target_oid)
        old_files = self.load_index()
        untracked = set(state.untracked)
        conflicts = sorted(
            path
            for path in untracked
            if any(
                path == target
                or path.startswith(target + "/")
                or target.startswith(path + "/")
                for target in target_files
            )
        )
        if conflicts:
            raise AgitError(f"checkout would overwrite untracked file: {conflicts[0]}")

        for path in sorted(set(old_files) - set(target_files), reverse=True):
            full_path = self.root / path
            if full_path.exists():
                full_path.unlink()
                self._remove_empty_parents(full_path.parent)

        for path, oid in target_files.items():
            full_path = self.root / path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(self._blob_bytes(oid))

        self.save_index(target_files)
        self._write_text(self.gitdir / "HEAD", f"ref: refs/heads/{name}\n")
        return name

    def pack_objects(self, prune: bool = False) -> PackResult:
        loose = self._loose_objects()
        if not loose:
            return PackResult(path=None, object_count=0, pruned=False)

        oids = sorted(loose)
        pack_name = "pack-" + hashlib.sha1("".join(oids).encode("ascii")).hexdigest()[:12]
        packs_dir = self.gitdir / "packs"
        pack_path = packs_dir / f"{pack_name}.pack"
        index_path = packs_dir / f"{pack_name}.idx.json"
        index: dict[str, dict[str, int]] = {}

        with pack_path.open("wb") as handle:
            handle.write(PACK_MAGIC)
            for oid in oids:
                compressed = loose[oid].read_bytes()
                handle.write(bytes.fromhex(oid))
                handle.write(struct.pack(">Q", len(compressed)))
                offset = handle.tell()
                handle.write(compressed)
                index[oid] = {"offset": offset, "size": len(compressed)}

        self._write_json(
            index_path,
            {"version": 1, "pack": pack_path.name, "objects": index},
        )

        # Verify every packed object before optionally removing its loose copy.
        for oid in oids:
            compressed = self._read_from_pack_index(index_path, oid)
            obj = decode_object(zlib.decompress(compressed))
            if object_id(obj) != oid:
                raise AgitError(f"packed object {oid} failed verification")

        if prune:
            for path in loose.values():
                path.unlink()
            for directory in (self.gitdir / "objects").iterdir():
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()

        return PackResult(path=pack_path, object_count=len(oids), pruned=prune)

    def _stage_file(self, full_path: Path, relative: str, index: dict[str, str]) -> None:
        blob = Blob(full_path.read_bytes())
        index[relative] = self.store_object(blob)

    def _head_files(self) -> dict[str, str]:
        oid = self.head_oid()
        return self._files_for_commit(oid) if oid else {}

    def _files_for_commit(self, oid: str) -> dict[str, str]:
        commit = self._expect_commit(oid)
        tree = self.read_object(commit.tree)
        if not isinstance(tree, Tree):
            raise AgitError(f"object {commit.tree} is not a tree")
        return {entry.path: entry.oid for entry in tree.entries}

    def _expect_commit(self, oid: str) -> Commit:
        obj = self.read_object(oid)
        if not isinstance(obj, Commit):
            raise AgitError(f"object {oid} is not a commit")
        return obj

    def _blob_bytes(self, oid: str) -> bytes:
        obj = self.read_object(oid)
        if not isinstance(obj, Blob):
            raise AgitError(f"object {oid} is not a blob")
        return obj.data

    @staticmethod
    def _compare_maps(old: dict[str, str], new: dict[str, str]) -> list[Change]:
        result: list[Change] = []
        for path in sorted(set(old) | set(new)):
            if path not in old:
                result.append(Change("A", path))
            elif path not in new:
                result.append(Change("D", path))
            elif old[path] != new[path]:
                result.append(Change("M", path))
        return result

    @staticmethod
    def _unified_diff(path: str, old: bytes, new: bytes) -> str:
        try:
            old_text = old.decode("utf-8").splitlines(keepends=True)
            new_text = new.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            return f"Binary file changed: {path}\n"
        lines = difflib.unified_diff(
            old_text,
            new_text,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
        text = "".join(lines)
        return text if text.endswith("\n") or not text else text + "\n"

    def _walk_files(self, start: Path) -> list[Path]:
        result: list[Path] = []
        for directory, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(name for name in dirnames if name not in IGNORED_DIRS)
            for filename in sorted(filenames):
                path = Path(directory) / filename
                relative = self._relative(path)
                if not self._is_ignored(relative) and not path.is_symlink():
                    result.append(path)
        return result

    def _ensure_inside_root(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise AgitError(f"path is outside the repository: {path}") from exc

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() or "."

    def _is_ignored(self, relative: str) -> bool:
        parts = Path(relative).parts
        return (
            any(part in IGNORED_DIRS for part in parts)
            or Path(relative).name in IGNORED_FILES
            or Path(relative).suffix == ".pyc"
        )

    def _ensure_not_ignored(self, relative: str) -> None:
        if self._is_ignored(relative):
            raise AgitError(f"path is ignored for safety: {relative}")

    def _loose_path(self, oid: str) -> Path:
        return self.gitdir / "objects" / oid[:2] / oid[2:]

    def _loose_objects(self) -> dict[str, Path]:
        result: dict[str, Path] = {}
        objects_dir = self.gitdir / "objects"
        for directory in objects_dir.iterdir():
            if not directory.is_dir() or len(directory.name) != 2:
                continue
            for path in directory.iterdir():
                oid = directory.name + path.name
                if path.is_file() and _is_oid(oid):
                    result[oid] = path
        return result

    def _read_packed(self, oid: str) -> bytes:
        for index_path in sorted((self.gitdir / "packs").glob("*.idx.json")):
            try:
                return self._read_from_pack_index(index_path, oid)
            except KeyError:
                continue
        raise AgitError(f"object not found: {oid}")

    def _read_from_pack_index(self, index_path: Path, oid: str) -> bytes:
        value = json.loads(index_path.read_text(encoding="utf-8"))
        entry = value["objects"][oid]
        pack_path = index_path.parent / value["pack"]
        with pack_path.open("rb") as handle:
            if handle.read(len(PACK_MAGIC)) != PACK_MAGIC:
                raise AgitError(f"invalid pack file: {pack_path.name}")
            handle.seek(entry["offset"])
            data = handle.read(entry["size"])
        if len(data) != entry["size"]:
            raise AgitError(f"truncated pack file: {pack_path.name}")
        return data

    def _remove_empty_parents(self, directory: Path) -> None:
        while directory != self.root:
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent

    @staticmethod
    def _validate_branch_name(name: str) -> None:
        valid = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", name)
        invalid_piece = any(piece in name for piece in ("..", "//", "@{"))
        if not valid or invalid_piece or name.endswith(("/", ".", ".lock")):
            raise AgitError(f"invalid branch name: {name}")

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    @classmethod
    def _write_json(cls, path: Path, value: object) -> None:
        cls._write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8")


def _is_oid(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{40}", value))
