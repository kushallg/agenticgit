# Agentic Git

Agentic Git is a small, readable version control system written in Python. It
stores snapshots using SHA-1 and lets Gemini translate plain English into a
strict set of safe version-control actions.

This is an educational project. It uses the same main ideas as Git, but its
`.agit` format is intentionally simpler and is **not compatible with Git**.

## Features

- SHA-1 content-addressed blob, tree, and commit objects
- A staging index, commits, history, branches, and checkout
- Staged and unstaged text diffs
- A small compressed pack format with an index
- Gemini natural-language commands
- No shell access for the model: Gemini can only select validated `agit` actions

## Install

Python 3.10 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

The installation creates the `agit` command. You can also run every command as
`python -m agentic_git`.

## Basic workflow

Run these commands inside any folder you want Agentic Git to track:

```bash
agit init
agit status
agit add .
agit commit -m "first version"

# Edit a file, then inspect and save the change.
agit diff
agit add .
agit diff --staged
agit commit -m "update notes"

agit log
agit branch feature
agit checkout feature
```

`agit add .` automatically skips `.agit`, `.git`, `.venv`, `__pycache__`, and
`.env`. This prevents repository internals, virtual environments, and API keys
from being staged accidentally.

## Gemini setup

Copy the example environment file and add your key:

```bash
cp .env.example .env
```

```dotenv
GEMINI_API_KEY=your_real_key
GEMINI_MODEL=gemini-2.5-flash
```

Then use natural language:

```bash
agit ai "show me what changed"
agit ai "add everything and commit it as first version"
agit ai "create a branch named experiment"
```

Gemini returns JSON containing at most five actions. The application validates
every command and argument before calling its own Python functions. Arbitrary
commands and shell execution are rejected.

## Object storage

The repository lives in `.agit/`:

```text
.agit/
├── HEAD
├── index.json
├── objects/
├── packs/
└── refs/
    └── heads/
```

Files become **blob** objects. The staging index becomes a **tree** object, and
a **commit** points to that tree and its parent commit. An object's identifier is
the SHA-1 of:

```text
<object type> <content length>\0<content>
```

Loose objects are compressed with zlib and stored under `.agit/objects/`.

## Pack files

Create a pack while retaining loose object copies:

```bash
agit pack
```

Or remove loose copies after every packed object passes an integrity check:

```bash
agit pack --prune
```

The pack implementation concatenates compressed objects and writes a JSON index
with byte offsets. It deliberately does not implement Git's delta compression.

## Commands

| Command | Purpose |
| --- | --- |
| `agit init [path]` | Create a repository |
| `agit add <paths...>` | Stage files, directories, or deletions |
| `agit status` | Show staged, unstaged, and untracked files |
| `agit commit -m "message"` | Commit the staged snapshot |
| `agit log [-n NUMBER]` | Show commit history |
| `agit diff [--staged]` | Show working or staged changes |
| `agit branch [name]` | List branches or create one |
| `agit checkout <name>` | Switch branches and restore files |
| `agit pack [--prune]` | Pack loose objects |
| `agit ai "request"` | Ask Gemini to choose safe actions |

Checkout refuses to run when tracked files have staged or unstaged changes. It
also refuses to overwrite an untracked file.

## Run tests

The tests use Python's standard library and do not call Gemini:

```bash
python -m unittest discover -v
```
