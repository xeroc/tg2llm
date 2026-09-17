# tg — Telegram sorting CLI for AI agents

`tg` is a single-file Python CLI that exposes a **Telegram user account** to AI agents (or humans): it lists contacts, chats and folders, reads recent messages, and sorts chats into folders. All output is JSON on stdout, all errors are JSON on stderr with a non-zero exit code — built to be shell-scripted by an agent loop:

```
tg chats  →  tg read <chat>  →  tg move <chat> --to <folder>
```

> [!IMPORTANT]
> This talks **MTProto as your own user account**, not the Bot API. Bots cannot see your contacts, your dialog list, or your folders — a user session is the only way this tool can exist. Automating your own account for reading/organizing is tolerated by Telegram; spamming is not.

## Key features

- **Inventory** — all dialogs with type, unread count, folder memberships, archive state and last-message preview in one call
- **Read** — recent messages of any chat (the classification input for your agent)
- **Folders** — list, create, and add chats to folders (additive; a chat may live in several folders)
- **Archive** — one call to archive/unarchive a chat
- **Agent-proof** — JSON everywhere, `--dry-run` on writes, FloodWait auto-retry, no interactive prompts unless you ask for them

## Tech stack

- **Language**: Python 3.12+
- **Telegram**: [Telethon 1.45](https://docs.telethon.dev/) (MTProto client library)
- **Packaging**: [uv](https://docs.astral.sh/uv/) + hatchling
- **Tests**: pytest (pure-logic tests against fake clients — no network)
- **Lint**: ruff
- **CLI**: stdlib `argparse`, zero other runtime deps

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/getting_started/install/)
- A Telegram account
- An `api_id` / `api_hash` pair — create once at [my.telegram.org](https://my.telegram.org) → _API development tools_

## Install (from PyPI)

The package is published as [`tg2llm`](https://pypi.org/project/tg2llm/); installing it puts the **`tg`** command on your PATH:

```bash
uv tool install tg2llm     # recommended (isolated env)
# or: pipx install tg2llm
# or: pip install tg2llm

tg --help                  # verify
```

Note the difference: the _package_ is `tg2llm`, the _command_ it installs is `tg` (defined via `[project.scripts]` in `pyproject.toml`).

Then log in once (interactive — phone + code + optional 2FA):

```bash
export TG_API_ID=... TG_API_HASH=...   # from my.telegram.org
tg auth
```

That's it — the session lands in `~/.config/tg-sort/session` and every later command runs non-interactive.

## Getting started from source (clone → using)

For development or customization:

```bash
git clone <this-repo> && cd Telegram

# 1. install deps into .venv
make setup

# 2. configure credentials
cp .env.example .env      # then edit: TG_API_ID, TG_API_HASH

# 3. one-time interactive login (phone + code + optional 2FA)
make auth

# 4. optional: install system-wide from this checkout
make install

# 5. use it
tg chats
```

The login in step 3 creates a session file at `~/.config/tg-sort/session` — after that, no more prompts, ever.

> [!WARNING]
> The session file **is** your Telegram account. Anyone who can read it can act as you. Keep it where it is, never commit/copy it (`*.session` is already gitignored).

<details>
<summary>No Makefile? Plain commands</summary>

```bash
uv sync                                   # deps
cp .env.example .env                      # edit credentials
export $(grep -v '^#' .env | xargs)       # or export manually
uv run python tg.py auth                  # login
uv tool install .                         # system-wide
```

</details>

## Make targets

| Target           | What it does                                                      |
| ---------------- | ----------------------------------------------------------------- |
| `make setup`     | `uv sync` — create/refresh `.venv`                                |
| `make auth`      | One-time interactive login                                        |
| `make install`   | Install `tg` system-wide from this checkout (`uv tool install .`) |
| `make uninstall` | Remove the system-wide binary                                     |
| `make test`      | Run the test suite                                                |
| `make lint`      | ruff checks                                                       |
| `make fmt`       | ruff autofix                                                      |
| `make check`     | lint + test — the gate every change must pass                     |
| `make clean`     | Remove caches                                                     |

The Makefile loads `.env` automatically, so `make auth` picks up your credentials without exporting anything.

## CLI reference

Every command prints JSON (stdout, `indent=1`). Errors print `{"error": "..."}` to stderr and exit `1`.

### `tg prime`

Prints the embedded agent skill: command reference, safety rules (writes require human confirmation + dry-run first), the sort workflow, and an example session. Feed this to your agent once and it knows how to drive the tool:

```bash
tg prime > /tmp/tg-skill.md
```

### `tg auth`

One-time interactive login. Prompts for phone number, login code, and 2FA password if set. Prints the logged-in user. Run this once per machine.

### `tg contacts`

```json
[
  {
    "id": 123456789,
    "name": "Jane Doe",
    "username": "janedoe",
    "phone": "4915012345678"
  }
]
```

### `tg chats [--folder NAME] [--limit N]`

All dialogs with folder memberships (from `--folder`, filtered client-side). This is the agent's inventory.

```json
[
  {
    "id": -1001701234567,
    "title": "Solana News",
    "type": "channel",
    "unread": 42,
    "folders": ["Crypto"],
    "archived": false,
    "last_message": {
      "date": "2026-09-17 09:12:00+00:00",
      "text": "Mainnet upgrade shipped —"
    }
  }
]
```

IDs are _marked_ peer IDs (negative for groups/channels) — pass them back verbatim to `read`/`move`/`archive`.

### `tg read CHAT [--last N]` (default 20)

Recent messages of a chat, oldest first.

```json
[
  {
    "id": 4812,
    "date": "2026-09-17 08:59:00+00:00",
    "sender_id": 987654321,
    "text": "deploy blocked on the audit, moving to Friday"
  }
]
```

### `tg folders`

```json
[
  {
    "id": 5,
    "title": "Crypto",
    "emoticon": null,
    "chats": [-1001701234567, -1009876543210]
  }
]
```

### `tg create-folder TITLE [--chat CHAT]`

Creates a folder (`TITLE` ≤ 12 chars, max 10 folders — Telegram's limits), optionally seeded with one chat.

### `tg move CHAT --to FOLDER [--create] [--dry-run]`

Adds a chat to a folder, **additively** — other folder memberships stay untouched. `FOLDER` is matched case-insensitively by title or by id. `--create` creates the folder if missing. `--dry-run` computes and prints the diff without sending anything.

```json
{
  "chat": -1001701234567,
  "folder": { "id": 6, "title": "AI News" },
  "action": "added",
  "dry_run": false,
  "include_count": { "before": 3, "after": 4 }
}
```

`action` is `"added"`, `"created+added"`, or `"already"` (idempotent, sends nothing).

### `tg archive CHAT [--undo]`

Moves a chat to the archive folder (or back with `--undo`).

## The agent sorting loop

```bash
tg chats                                   # 1. inventory + current folders
tg read -1001701234567 --last 20           # 2. inspect an unsorted chat
tg move -1001701234567 --to "AI News" --dry-run   # 3. preview
tg move -1001701234567 --to "AI News" --create    # 4. apply (creates folder if needed)
```

Recommended agent policy: dry-run first, batch many moves per run, sleep briefly between calls to stay far away from rate limits.

## Environment variables

| Variable      | Required | Default                     | Purpose                       |
| ------------- | -------- | --------------------------- | ----------------------------- |
| `TG_API_ID`   | yes      | —                           | API id from my.telegram.org   |
| `TG_API_HASH` | yes      | —                           | API hash from my.telegram.org |
| `TG_SESSION`  | no       | `~/.config/tg-sort/session` | Telethon session file path    |

## Architecture

### Project layout

```
├── tg.py        # everything: CLI, Telethon plumbing, pure helpers
├── test_tg.py   # pytest suite, FakeClient-based — no network
├── tg           # dev wrapper: .venv python on tg.py (symlink-free)
├── Makefile     # setup / auth / install / test / lint targets
├── pyproject.toml  # uv + hatchling; [project.scripts] tg = "tg:main"
└── .env.example
```

### How folder sorting actually works

Telegram folders ("dialog filters") are **saved views, not containers**. A folder is a `DialogFilter` object: rule flags (all groups / all unmuted / …) plus explicit `include_peers` / `exclude_peers` lists. Adding a chat to a folder means:

1. fetch all filters — `messages.GetDialogFiltersRequest`
2. locate the target folder (by id or title)
3. append the chat's `InputPeer` to `include_peers`
4. send the whole filter back — `messages.UpdateDialogFilterRequest(id, filter)`

Because folders are views, a chat can be in several at once — that's why `move` is additive by design. Exclusive sorting would mean removing the peer from every other filter's `include_peers` first.

### Key pieces in `tg.py`

| Piece                   | Why it exists                                                                                                                                                                                                                     |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `session()` ctx manager | `async with client` in Telethon calls `start()`, which **prompts interactively** if unauthorized — this would hang an agent. `session()` connects, checks `is_user_authorized()`, and fails fast with a clean JSON error instead. |
| `call()` wrapper        | Catches `FloodWaitError`, sleeps the requested seconds, retries once                                                                                                                                                              |
| `filter_title()`        | Folder titles are `TextWithEntities` since Folders 2.0 — helper extracts the plain text                                                                                                                                           |
| `unwrap_filters()`      | Different API layers return either a bare list or a wrapper with `.filters` — handled once here                                                                                                                                   |
| `find_filter()`         | Case-insensitive title match _or_ numeric id match                                                                                                                                                                                |
| `move_chat()`           | The whole sort primitive: resolve entity → locate folder → idempotency check → append peer → send. Pure enough to test against a fake client.                                                                                     |

### Limits enforced (Telegram's, not ours)

- 10 folders max, folder titles ≤ 12 chars
- Marked IDs: users are positive ints, groups `-…`, channels/supergroups `-100…`

## Testing

```bash
make test          # 15 tests
uv run pytest -q test_tg.py::test_move_dry_run_no_call   # single test
```

The suite covers the pure helpers (filter lookup, title extraction, id allocation, membership) and the `move_chat` primitive against a `FakeClient` that records requests — no network, no account needed. The Telethon I/O shell is verified by the smoke path: `tg chats` on a real session.

## Troubleshooting

| Symptom                                         | Fix                                                                                                                                    |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `{"error": "set TG_API_ID and TG_API_HASH..."}` | `cp .env.example .env`, fill in values; export them in shells that don't go through the Makefile                                       |
| `{"error": "not logged in - run: ./tg auth"}`   | Session missing/expired on this machine — `make auth`                                                                                  |
| Command hangs forever                           | You're probably calling `tg auth` from a non-interactive context. `auth` is the only command that prompts; run it once from a terminal |
| `FloodWaitError` in output                      | The wrapper retries once automatically; if you still see it, slow the agent loop down (sleep between moves)                            |
| `tg: command not found` after `make install`    | `~/.local/bin` not on `PATH` — add `export PATH="$HOME/.local/bin:$PATH"`                                                              |
| Updates not picked up after code changes        | If installed from PyPI: `uv tool upgrade tg2llm`. If from a checkout: `make install` (runs `uv tool install --upgrade .`)              |
| Login SMS never arrives                         | Try login code via Telegram app (option appears after the phone step)                                                                  |

## Security notes

- `.env` and session files are gitignored; the session grants full account access — treat it like a password
- The tool only ever _reads_ chats and _edits your own folder/archive state_. It never sends messages, so it cannot spam on your behalf
- Revoking access: Telegram → Settings → Devices → terminate the session (then delete `~/.config/tg-sort/session`)

## Uninstall

```bash
uv tool uninstall tg2llm    # or: pipx uninstall tg2llm / pip uninstall tg2llm
rm -rf ~/.config/tg-sort    # session + credentials cache
```
