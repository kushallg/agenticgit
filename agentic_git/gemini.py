"""Translate plain English into a short list of validated Agentic Git actions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from .repository import AgitError


ALLOWED_COMMANDS = {
    "add",
    "status",
    "commit",
    "log",
    "diff",
    "branch",
    "checkout",
    "pack",
}


@dataclass(frozen=True)
class Action:
    command: str
    args: dict[str, Any]


SYSTEM_PROMPT = """You translate a user's request into Agentic Git actions.
Return JSON only, with this exact top-level shape:
{"actions": [{"command": "status", "args": {}}]}

Allowed actions and arguments:
- add: {"paths": ["path", "."]}
- status: {}
- commit: {"message": "message"}
- log: {}
- diff: {"staged": false}
- branch: {"name": "branch-name"}
- checkout: {"name": "branch-name"}
- pack: {"prune": false}

Use at most five actions. Resolve obvious wording such as "everything" to path ".".
Do not invent paths, messages, or branch names. If the request is ambiguous or
cannot be represented, return {"actions": []}.

User request:
"""


def translate(prompt: str, env_path: Path | None = None) -> list[Action]:
    """Call Gemini, then validate its response before any action is executed."""
    try:
        from dotenv import load_dotenv
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise AgitError("Gemini dependencies are missing; run 'pip install -e .'") from exc

    load_dotenv(dotenv_path=env_path or Path.cwd() / ".env")
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise AgitError("set GEMINI_API_KEY in your environment or .env file")

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=SYSTEM_PROMPT + prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
    except Exception as exc:
        raise AgitError(f"Gemini request failed: {exc}") from exc
    finally:
        client.close()

    if not response.text:
        raise AgitError("Gemini returned an empty response")
    return parse_actions(response.text)


def parse_actions(text: str) -> list[Action]:
    """Parse and strictly validate the small action language."""
    try:
        value = json.loads(text)
        raw_actions = value["actions"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AgitError("Gemini returned invalid JSON") from exc

    if not isinstance(raw_actions, list) or len(raw_actions) > 5:
        raise AgitError("Gemini returned an invalid number of actions")

    actions: list[Action] = []
    for raw in raw_actions:
        if not isinstance(raw, dict) or set(raw) != {"command", "args"}:
            raise AgitError("Gemini returned an invalid action")
        command = raw["command"]
        args = raw["args"]
        if (
            not isinstance(command, str)
            or command not in ALLOWED_COMMANDS
            or not isinstance(args, dict)
        ):
            raise AgitError("Gemini returned an unsupported action")
        _validate_args(command, args)
        actions.append(Action(command=command, args=args))
    return actions


def _validate_args(command: str, args: dict[str, Any]) -> None:
    if command in {"status", "log"}:
        _require_keys(args, set())
    elif command == "add":
        _require_keys(args, {"paths"})
        paths = args["paths"]
        if not isinstance(paths, list) or not paths or not all(
            isinstance(path, str) and path for path in paths
        ):
            raise AgitError("Gemini returned invalid paths for add")
    elif command == "commit":
        _require_string(args, "message")
    elif command in {"branch", "checkout"}:
        _require_string(args, "name")
    elif command == "diff":
        _require_bool(args, "staged")
    elif command == "pack":
        _require_bool(args, "prune")


def _require_keys(args: dict[str, Any], keys: set[str]) -> None:
    if set(args) != keys:
        raise AgitError("Gemini returned unexpected action arguments")


def _require_string(args: dict[str, Any], key: str) -> None:
    _require_keys(args, {key})
    if not isinstance(args[key], str) or not args[key].strip():
        raise AgitError(f"Gemini returned an invalid {key}")


def _require_bool(args: dict[str, Any], key: str) -> None:
    _require_keys(args, {key})
    if not isinstance(args[key], bool):
        raise AgitError(f"Gemini returned an invalid {key}")
