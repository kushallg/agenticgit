"""The three object types stored by Agentic Git.

Every object is serialized, prefixed with a small header, and identified by the
SHA-1 hash of those bytes. This is the same core content-addressing idea Git
uses, while the tree and commit payloads use readable JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import ClassVar, TypeAlias


class ObjectFormatError(ValueError):
    """Raised when stored object data is malformed."""


@dataclass(frozen=True)
class Blob:
    data: bytes
    kind: ClassVar[str] = "blob"

    def to_bytes(self) -> bytes:
        return self.data

    @classmethod
    def from_bytes(cls, data: bytes) -> "Blob":
        return cls(data=data)


@dataclass(frozen=True)
class TreeEntry:
    path: str
    oid: str


@dataclass(frozen=True)
class Tree:
    entries: tuple[TreeEntry, ...]
    kind: ClassVar[str] = "tree"

    def to_bytes(self) -> bytes:
        items = [
            {"path": entry.path, "oid": entry.oid}
            for entry in sorted(self.entries, key=lambda entry: entry.path)
        ]
        return _json_bytes(items)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Tree":
        try:
            items = json.loads(data.decode("utf-8"))
            if not isinstance(items, list):
                raise TypeError
            entries = tuple(
                TreeEntry(path=_text(item, "path"), oid=_oid(item, "oid"))
                for item in items
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ObjectFormatError("invalid tree object") from exc
        return cls(entries=entries)


@dataclass(frozen=True)
class Commit:
    tree: str
    parent: str | None
    message: str
    timestamp: str
    kind: ClassVar[str] = "commit"

    def to_bytes(self) -> bytes:
        return _json_bytes(
            {
                "tree": self.tree,
                "parent": self.parent,
                "message": self.message,
                "timestamp": self.timestamp,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "Commit":
        try:
            item = json.loads(data.decode("utf-8"))
            if not isinstance(item, dict):
                raise TypeError
            parent = item.get("parent")
            if parent is not None:
                parent = _checked_oid(parent)
            return cls(
                tree=_oid(item, "tree"),
                parent=parent,
                message=_text(item, "message"),
                timestamp=_text(item, "timestamp"),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ObjectFormatError("invalid commit object") from exc


StoredObject: TypeAlias = Blob | Tree | Commit


def encode_object(obj: StoredObject) -> bytes:
    """Return the exact uncompressed bytes that are hashed and stored."""
    body = obj.to_bytes()
    header = f"{obj.kind} {len(body)}\0".encode("ascii")
    return header + body


def object_id(obj: StoredObject) -> str:
    return hashlib.sha1(encode_object(obj)).hexdigest()


def decode_object(raw: bytes) -> StoredObject:
    try:
        header, body = raw.split(b"\0", 1)
        kind_bytes, size_bytes = header.split(b" ", 1)
        kind = kind_bytes.decode("ascii")
        size = int(size_bytes)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ObjectFormatError("invalid object header") from exc

    if size != len(body):
        raise ObjectFormatError("object size does not match its header")

    factories = {
        "blob": Blob.from_bytes,
        "tree": Tree.from_bytes,
        "commit": Commit.from_bytes,
    }
    try:
        return factories[kind](body)
    except KeyError as exc:
        raise ObjectFormatError(f"unknown object type: {kind}") from exc


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _text(item: dict[str, object], key: str) -> str:
    value = item[key]
    if not isinstance(value, str):
        raise TypeError
    return value


def _oid(item: dict[str, object], key: str) -> str:
    return _checked_oid(_text(item, key))


def _checked_oid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise TypeError
    try:
        int(value, 16)
    except ValueError as exc:
        raise TypeError from exc
    return value

