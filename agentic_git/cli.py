"""Command-line interface for Agentic Git."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .gemini import Action, translate
from .repository import AgitError, Repository, Status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agit",
        description="A tiny Git-like version control system with a Gemini interface.",
    )
    parser.add_argument("--version", action="version", version="agentic-git 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create an empty .agit repository")
    init_parser.add_argument("path", nargs="?", default=".")

    add_parser = subparsers.add_parser("add", help="add file contents to the staging index")
    add_parser.add_argument("paths", nargs="+")

    subparsers.add_parser("status", help="show staged and working-tree changes")

    commit_parser = subparsers.add_parser("commit", help="record the staged snapshot")
    commit_parser.add_argument("-m", "--message", required=True)

    log_parser = subparsers.add_parser("log", help="show commit history")
    log_parser.add_argument("-n", "--limit", type=_positive_int)

    diff_parser = subparsers.add_parser("diff", help="show line-by-line changes")
    diff_parser.add_argument("--staged", action="store_true")

    branch_parser = subparsers.add_parser("branch", help="list or create branches")
    branch_parser.add_argument("name", nargs="?")

    checkout_parser = subparsers.add_parser("checkout", help="switch to an existing branch")
    checkout_parser.add_argument("name")

    pack_parser = subparsers.add_parser("pack", help="combine loose objects into a pack file")
    pack_parser.add_argument(
        "--prune",
        action="store_true",
        help="remove loose objects after the pack is verified",
    )

    ai_parser = subparsers.add_parser("ai", help="turn plain English into safe agit actions")
    ai_parser.add_argument("prompt", nargs="+")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            repo, created = Repository.init(args.path)
            word = "Initialized" if created else "Reinitialized"
            print(f"{word} Agentic Git repository in {repo.gitdir}")
            return 0

        repo = Repository.discover()
        if args.command == "add":
            paths = repo.add(args.paths, cwd=Path.cwd())
            print(f"Staged {len(paths)} path(s).")
        elif args.command == "status":
            _print_status(repo, repo.status())
        elif args.command == "commit":
            oid, commit = repo.commit(args.message)
            print(f"[{repo.current_branch} {oid[:7]}] {commit.message}")
        elif args.command == "log":
            _print_log(repo, args.limit)
        elif args.command == "diff":
            print(repo.diff(staged=args.staged), end="")
        elif args.command == "branch":
            if args.name:
                oid = repo.create_branch(args.name)
                print(f"Created branch '{args.name}' at {oid[:7]}.")
            else:
                _print_branches(repo)
        elif args.command == "checkout":
            repo.checkout(args.name)
            print(f"Switched to branch '{args.name}'.")
        elif args.command == "pack":
            _run_pack(repo, args.prune)
        elif args.command == "ai":
            actions = translate(" ".join(args.prompt), env_path=repo.root / ".env")
            if not actions:
                raise AgitError("Gemini could not map that request to a safe action")
            for action in actions:
                print(f"> {action.command}")
                _run_action(repo, action)
        return 0
    except AgitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _run_action(repo: Repository, action: Action) -> None:
    command, args = action.command, action.args
    if command == "add":
        changed = repo.add(args["paths"], cwd=Path.cwd())
        print(f"Staged {len(changed)} path(s).")
    elif command == "status":
        _print_status(repo, repo.status())
    elif command == "commit":
        oid, commit = repo.commit(args["message"])
        print(f"[{repo.current_branch} {oid[:7]}] {commit.message}")
    elif command == "log":
        _print_log(repo, None)
    elif command == "diff":
        print(repo.diff(staged=args["staged"]), end="")
    elif command == "branch":
        oid = repo.create_branch(args["name"])
        print(f"Created branch '{args['name']}' at {oid[:7]}.")
    elif command == "checkout":
        repo.checkout(args["name"])
        print(f"Switched to branch '{args['name']}'.")
    elif command == "pack":
        _run_pack(repo, args["prune"])


def _print_status(repo: Repository, status: Status) -> None:
    print(f"On branch {repo.current_branch}")
    if status.clean:
        print("Working tree clean.")
        return
    if status.staged:
        print("\nChanges staged for commit:")
        for change in status.staged:
            print(f"  {change.code} {change.path}")
    if status.unstaged:
        print("\nChanges not staged:")
        for change in status.unstaged:
            print(f"  {change.code} {change.path}")
    if status.untracked:
        print("\nUntracked files:")
        for path in status.untracked:
            print(f"  ? {path}")


def _print_log(repo: Repository, limit: int | None) -> None:
    history = repo.log(limit)
    if not history:
        print("No commits yet.")
        return
    for index, (oid, commit) in enumerate(history):
        if index:
            print()
        print(f"commit {oid}")
        print(f"Date:   {commit.timestamp}")
        print(f"\n    {commit.message}")


def _print_branches(repo: Repository) -> None:
    for name in repo.branches():
        marker = "*" if name == repo.current_branch else " "
        print(f"{marker} {name}")


def _run_pack(repo: Repository, prune: bool) -> None:
    result = repo.pack_objects(prune=prune)
    if result.path is None:
        print("No loose objects to pack.")
        return
    suffix = " and removed loose copies" if result.pruned else ""
    print(f"Packed {result.object_count} object(s) into {result.path.name}{suffix}.")


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


if __name__ == "__main__":
    raise SystemExit(main())
