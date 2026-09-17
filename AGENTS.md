# AGENTS.md

## Project Overview

`tg` is a single-file Python CLI (`tg.py`, ~340 lines) that exposes a Telegram **user account** via MTProto (Telethon) so an AI agent can inventory chats/contacts, read recent messages, and sort chats into folders. All output is JSON; all write paths are additive and dry-runnable. There is deliberately no framework, no package layout, no second module.

## Setup Commands

```bash
make setup     # uv sync — creates .venv, installs telethon + pytest
make install   # system-wide binary via `uv tool install .` (project.scripts: tg = "tg:main")
```

Credentials: `cp .env.example .env` and fill `TG_API_ID`/`TG_API_HASH` (from my.telegram.org). The Makefile auto-exports `.env`. Live login is interactive and human-only: `make auth` (session lands in `~/.config/tg-sort/session`). Without credentials/login every command exits fast with a JSON error — that is by design, not a bug.

## Verification Gate

Every change must pass:

```bash
make check     # ruff check + pytest, in that order
```

No CI exists yet; the gate is local only.

## Testing Instructions

- Run all: `make test` (or `uv run pytest -q`)
- Single test: `uv run pytest -q test_tg.py::test_move_adds_peer_additively`
- Tests are **offline by design**: pure helpers are exercised directly, and `move_chat()` runs against `FakeClient` (records every request; returns canned filters). Never add a test that needs a real session or network.
- New write-path logic (folder mutation, archive, anything sending a request) must get a `--dry-run`-style test asserting **zero requests sent**, plus a happy-path test asserting the exact request type (`functions.messages.UpdateDialogFilterRequest`).
- TDD: write the failing test first, watch it fail, then implement.

## Code Style

- Stdlib only for the CLI (`argparse`, `asyncio`, `contextlib`, `json`). Telethon is the only runtime dependency.
- One file. Do not split `tg.py` into a package unless it hurts.
- No comments unless asked; function/variable names carry the meaning.
- Structure to preserve, top to bottom: constants → `CliError` → output/transport helpers (`out`, `call`, `session`) → pure helpers (`unwrap_filters`, `filter_title`, `find_filter`, `next_filter_id`, `new_filter`, `folder_membership`, `chat_row`, `parse_chat`) → `move_chat` → command handlers → `main()`.
- Pure helpers must stay Telethon-importable-but-callable without a client — that's what makes them testable.
- Errors: raise `CliError` with a message; `main()` turns it into `{"error": ...}` + exit 1. Never `sys.exit` deep in helpers.

## Build / Install

`uv tool install --upgrade .` builds a wheel (hatchling, `only-include = ["tg.py"]` — flat module, this flag is load-bearing) and links `~/.local/bin/tg`. Rerun after any `tg.py` change for the installed binary to pick it up. Package name is `tg2llm` (avoids PyPI collision with GramJS's `telegram`).

## Telethon Gotchas (the load-bearing knowledge)

1. **`async with client` calls `start()`**, which prompts interactively on stdin when unauthorized — an agent process hangs forever. All non-auth commands use the `session()` contextmanager: `connect()` → `is_user_authorized()` check → fail fast. Only `cmd_auth` may call `start()`.
2. **Name collision:** `types.UpdateDialogFilter` is an _update event_; the request is `functions.messages.UpdateDialogFilterRequest`. Wrong namespace = isinstance checks silently fail.
3. **Folder titles are `TextWithEntities`** (Folders 2.0), not strings. Always extract text via `filter_title()`; when constructing, pass `types.TextPlain(title)`. Server rejects full-filter updates that drop fields — send the whole `DialogFilter` back, not a partial.
4. **Folders are saved views, not containers**: membership = presence in `DialogFilter.include_peers`. "Move" is additive append + `UpdateDialogFilterRequest(id, filter)`. Chats may legitimately live in multiple folders.
5. **IDs are marked**: users positive, groups negative, channels/supergroups `-100…`. Compare with `telethon.utils.get_peer_id()` on both sides before any equality check.
6. **API drift defense:** `unwrap_filters()` handles both a bare filter list and a wrapper exposing `.filters` — keep it that way when touching folder code.
7. FloodWait: all requests go through `call()` which sleeps `e.seconds + 1` and retries once. Don't bypass it.

## LSP Noise

The editor reports ~8 type errors in `tg.py` (TextPlain assignability, `start()`/`get_me()` return types, `reversed()` on TotalList). These are Telethon stub-inference gaps, runtime-verified by the test suite. Suppress with narrow `# type: ignore[union-attr]` comments like the tests do — do not "fix" them by restructuring working code.

## Debugging

- Smoke path after changes: `./tg --help`, then `tg chats` against a real session.
- `tg move <chat> --to <folder> --dry-run` is the safe probe for folder logic.
- Session file `~/.config/tg-sort/session` is the account credential — never read, copy, or commit it; `*.session` is gitignored.
- To test against a scratch account: `TG_SESSION=~/.config/tg-sort/session-test` isolates the session.

## PR / Change Checklist

- `make check` green (lint errors are fixed, never skipped — no "pre-existing" excuses)
- New logic got a failing-test-first pass
- Write paths: dry-run test included, request counts asserted
- README updated if the CLI surface, env vars, or limits changed
