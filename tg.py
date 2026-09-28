#!/usr/bin/env python3
"""tg - Telegram user-account CLI for agents. JSON out.

Env: TG_API_ID, TG_API_HASH (https://my.telegram.org/apps), TG_SESSION (optional).
"""
import argparse
import asyncio
import contextlib
import inspect
import json
import os
import sys

from telethon import TelegramClient, functions, types, utils
from telethon.errors import FloodWaitError

ARCHIVE_ID = 1
MAX_FOLDERS = 10
MAX_TITLE = 12
API_ID = int(os.environ.get("TG_API_ID", "0") or 0)
API_HASH = os.environ.get("TG_API_HASH", "")
SESSION = os.environ.get(
    "TG_SESSION", os.path.expanduser("~/.config/tg-sort/session")
)

MEDIA_DIR = os.path.expanduser("~/.cache/tg2llm/media")

PRIME = """\
# tg — Telegram folder-sorting skill

CLI over a Telegram USER account (MTProto). Reads chats/contacts, sorts chats
into folders. JSON on stdout; errors {"error": ...} on stderr, exit 1.

## Safety rules (MANDATORY)

- Write commands are: create-folder, move (without --dry-run), archive.
- NEVER send a write command without human confirmation.
- Before every write: show the human the exact plan (chat titles -> target
  folders), run the intent with --dry-run, present the result, wait for an
  explicit OK. No OK = no write.
- Read commands (chats, contacts, read, folders, prime) change nothing. Use
  them freely.
- The MCP server (tg mcp) additionally exposes send_message, react and
  mark_read — real writes, visible to peers. Same confirm rule applies.

## Commands

  tg chats [--folder F] [--limit N]   all dialogs: id, title, type, unread,
                                      folders, archived, last_message
  tg read CHAT [--last N]             N most recent messages, oldest first
  tg contacts                         contact list
  tg folders                          folders with member chat ids
  tg create-folder TITLE [--chat C]   new folder (TITLE <= 12 chars, max 10)
  tg move CHAT --to F [--create]      add CHAT to folder F, ADDITIVE
                                      [--dry-run: plan only, sends nothing]
  tg archive CHAT [--undo]            archive / unarchive a chat
  tg mcp                             MCP server on stdio (13 tools: this
                                     surface + send/react/mark-read/media;
                                     spawned by an MCP client, not run by hand)

Rules:
- CHAT is a marked id from `tg chats` (users >0, groups <0, channels -100...)
  or @username. Pass ids back verbatim.
- F matches a folder by title (case-insensitive) or id. --create makes a
  missing folder; folder names max 12 chars.
- move is additive: a chat may live in several folders. "already" = no-op.
- Throttle: sleep between writes, honor FloodWait.

## Sort workflow

1. Inventory:  tg chats
2. Classify:   pick chats with empty or wrong folders, inspect content:
               tg read <chat_id> --last 20
3. Plan:       check existing folders (tg folders), assign each chat a folder
4. CONFIRM:    show the human the plan + dry-run each move:
               tg move <chat_id> --to "<folder>" --dry-run
5. Apply only after explicit human approval:
               tg move <chat_id> --to "<folder>"
6. Optionally archive noise channels (same confirm rule):
               tg archive <chat_id>

## Example session

$ tg chats
[
 { "id": -1001701234567, "title": "Solana News", "type": "channel",
   "unread": 42, "folders": [], "archived": false,
   "last_message": { "date": "...", "text": "Mainnet upgrade shipped" } }
]

$ tg read -1001701234567 --last 3
[
 { "id": 4812, "date": "...", "sender_id": 987654321,
   "text": "SOL validators: upgrade at epoch 520" }
]

# -> propose to human: "Solana News" -> folder "Crypto" ; wait for OK

$ tg move -1001701234567 --to Crypto --dry-run
{
 "chat": -1001701234567,
 "folder": { "id": 5, "title": "Crypto" },
 "action": "added",
 "dry_run": true,
 "include_count": { "before": 3, "after": 4 }
}

# human confirms -> apply
$ tg move -1001701234567 --to Crypto
"""


class CliError(Exception):
    pass


def out(obj):
    print(json.dumps(obj, default=str, ensure_ascii=False, indent=1))


async def call(client, request):
    try:
        return await client(request)
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        return await client(request)


def unwrap_filters(result):
    fs = getattr(result, "filters", None)
    return list(fs) if fs is not None else list(result or [])


def custom_filters(filters):
    return [f for f in filters if isinstance(f, types.DialogFilter)]


def filter_title(f):
    t = getattr(f, "title", "")
    return getattr(t, "text", t)


def find_filter(filters, name_or_id):
    s = str(name_or_id).strip()
    for f in custom_filters(filters):
        if str(f.id) == s or filter_title(f).lower() == s.lower():
            return f
    return None


def next_filter_id(filters):
    ids = [f.id for f in custom_filters(filters)]
    return max(ids, default=1) + 1


def new_filter(fid, title):
    if len(title) > MAX_TITLE:
        raise CliError(f"folder title >{MAX_TITLE} chars: {title!r}")
    return types.DialogFilter(
        id=fid,
        title=types.TextWithEntities(text=title, entities=[]),
        pinned_peers=[],
        include_peers=[],
        exclude_peers=[],
    )


def folder_membership(filters):
    mem = {}
    for f in custom_filters(filters):
        for p in f.include_peers or []:
            mem.setdefault(utils.get_peer_id(p), []).append(filter_title(f))
    return mem


def chat_row(d, mem):
    m = d.message
    return {
        "id": d.id,
        "title": d.name,
        "type": "user" if d.is_user else "group" if d.is_group else "channel",
        "unread": d.unread_count,
        "folders": mem.get(d.id, []),
        "archived": bool(getattr(d, "archived", False)),
        "last_message": {
            "date": m.date,
            "text": (getattr(m, "raw_text", "") or "")[:80],
        }
        if m
        else None,
    }


def parse_chat(x):
    x = str(x)
    return int(x) if x.lstrip("-").isdigit() else x


def make_client():
    if not API_ID or not API_HASH:
        raise CliError("set TG_API_ID and TG_API_HASH (from my.telegram.org/apps)")
    os.makedirs(os.path.dirname(SESSION), exist_ok=True)
    return TelegramClient(SESSION, API_ID, API_HASH)


async def must_auth(c):
    if not await c.is_user_authorized():
        raise CliError("not logged in - run: ./tg auth")


@contextlib.asynccontextmanager
async def session():
    """Connect without start() - no interactive prompt hang for agents."""
    c = make_client()
    await c.connect()
    try:
        await must_auth(c)
        yield c
    finally:
        await c.disconnect()


async def fetch_filters(c):
    return unwrap_filters(await call(c, functions.messages.GetDialogFiltersRequest()))


async def move_chat(c, chat_id, target, create=False, dry=False):
    ent = await c.get_entity(parse_chat(chat_id))
    ip = utils.get_input_peer(ent)
    marked = utils.get_peer_id(ip)
    filters = await fetch_filters(c)
    f = find_filter(filters, target)
    created = False
    if f is None:
        if not create:
            raise CliError(f"no folder {target!r} - use --create")
        if len(custom_filters(filters)) >= MAX_FOLDERS:
            raise CliError(f"folder limit reached ({MAX_FOLDERS})")
        f = new_filter(next_filter_id(filters), str(target))
        created = True
    if marked in {utils.get_peer_id(p) for p in f.include_peers or []}:
        return {"chat": marked, "folder": filter_title(f), "action": "already"}
    before = len(f.include_peers or [])
    f.include_peers = list(f.include_peers or []) + [ip]
    if not dry:
        await call(c, functions.messages.UpdateDialogFilterRequest(id=f.id, filter=f))
    return {
        "chat": marked,
        "folder": {"id": f.id, "title": filter_title(f)},
        "action": ("created+" if created else "") + "added",
        "dry_run": dry,
        "include_count": {"before": before, "after": len(f.include_peers)},
    }


# --- shared cores (CLI + MCP) ------------------------------------------------


def _msg_row(m):
    media = getattr(m, "media", None)
    return {
        "id": m.id,
        "date": m.date,
        "sender_id": m.sender_id,
        "text": getattr(m, "raw_text", "") or "",
        "media": type(media).__name__ if media else None,
    }


async def me_row(c) -> dict:
    me = await c.get_me()
    return {
        "id": me.id,
        "name": " ".join(filter(None, [me.first_name, me.last_name])),
        "username": me.username,
        "phone": me.phone,
    }


async def chats_rows(c, folder: str | None = None, limit: int | None = None) -> list[dict]:
    mem = folder_membership(await fetch_filters(c))
    rows = [chat_row(d, mem) async for d in c.iter_dialogs(limit=limit)]
    if folder:
        rows = [r for r in rows if folder in r["folders"]]
    return rows


async def history_rows(
    c, chat: int | str, limit: int = 20, before_id: int | None = None
) -> list[dict]:
    msgs = await c.get_messages(parse_chat(chat), limit=limit, offset_id=before_id or 0)
    return [_msg_row(m) for m in reversed(msgs)]


async def search_rows(
    c, query: str, chat: int | str | None = None, limit: int = 20
) -> list[dict]:
    if chat is not None:
        ent = await c.get_entity(parse_chat(chat))
        found = [m async for m in c.iter_messages(ent, search=query, limit=limit)]
        found.reverse()
    else:
        # ponytail: single-page global search; add offset pagination if it falls short
        res = await call(
            c,
            functions.messages.SearchGlobalRequest(
                q=query,
                filter=types.InputMessagesFilterEmpty(),
                min_date=None,
                max_date=None,
                offset_rate=0,
                offset_id=0,
                offset_peer=types.InputPeerEmpty(),
                limit=limit,
            ),
        )
        found = list(getattr(res, "messages", []))
    rows = []
    for m in found:
        r = _msg_row(m)
        r["chat_id"] = getattr(m, "chat_id", None)
        rows.append(r)
    return rows


async def contacts_rows(c) -> list[dict]:
    res = await call(c, functions.contacts.GetContactsRequest(hash=0))
    return [
        {
            "id": u.id,
            "name": " ".join(filter(None, [u.first_name, u.last_name])),
            "username": u.username,
            "phone": u.phone,
        }
        for u in getattr(res, "users", [])
    ]


async def folders_rows(c) -> list[dict]:
    return [
        {
            "id": f.id,
            "title": filter_title(f),
            "emoticon": f.emoticon,
            "chats": [utils.get_peer_id(p) for p in f.include_peers or []],
        }
        for f in custom_filters(await fetch_filters(c))
    ]


async def download_media(c, chat: int | str, message_id: int) -> dict:
    os.makedirs(MEDIA_DIR, exist_ok=True)
    m = await c.get_messages(parse_chat(chat), ids=[int(message_id)])
    m = m[0] if isinstance(m, list) else m
    if m is None or not getattr(m, "media", None):
        raise CliError(f"message {message_id} has no media")
    path = await c.download_media(m, file=MEDIA_DIR)
    return {"message_id": int(message_id), "path": str(path)}


async def send_message(
    c,
    chat: int | str,
    text: str,
    reply_to: int | None = None,
    file: str | None = None,
) -> dict:
    ent = await c.get_entity(parse_chat(chat))
    msg = await c.send_message(ent, message=text, reply_to=reply_to, file=file)
    return {"id": msg.id, "chat": utils.get_peer_id(ent)}


async def react(c, chat: int | str, message_id: int, emoji: str = "") -> dict:
    ent = await c.get_entity(parse_chat(chat))
    reaction = [types.ReactionEmoji(emoticon=emoji)] if emoji else []
    await call(
        c,
        functions.messages.SendReactionRequest(
            peer=utils.get_input_peer(ent), msg_id=int(message_id), reaction=reaction
        ),
    )
    return {
        "chat": utils.get_peer_id(ent),
        "message_id": int(message_id),
        "reaction": emoji or None,
    }


async def mark_read(c, chat: int | str) -> dict:
    ent = await c.get_entity(parse_chat(chat))
    if isinstance(ent, types.Channel):
        req = functions.channels.ReadHistoryRequest(
            channel=utils.get_input_channel(ent), max_id=0
        )
    else:
        req = functions.messages.ReadHistoryRequest(
            peer=utils.get_input_peer(ent), max_id=0
        )
    await call(c, req)
    return {"chat": utils.get_peer_id(ent), "read": True}


async def archive_chat(c, chat: int | str, undo: bool = False) -> dict:
    ip = utils.get_input_peer(await c.get_entity(parse_chat(chat)))
    fid = 0 if undo else ARCHIVE_ID
    await call(
        c,
        functions.folders.EditPeerFoldersRequest(
            folder_peers=[types.InputFolderPeer(peer=ip, folder_id=fid)]
        ),
    )
    return {"chat": utils.get_peer_id(ip), "folder_id": fid}


async def create_folder(c, title: str, chat: int | str | None = None) -> dict:
    filters = await fetch_filters(c)
    if len(custom_filters(filters)) >= MAX_FOLDERS:
        raise CliError(f"folder limit reached ({MAX_FOLDERS})")
    f = new_filter(next_filter_id(filters), title)
    if chat:
        ip = utils.get_input_peer(await c.get_entity(parse_chat(chat)))
        f.include_peers = [ip]
    await call(c, functions.messages.UpdateDialogFilterRequest(id=f.id, filter=f))
    return {"created": {"id": f.id, "title": filter_title(f)}}


async def move_to_folder(
    c, chat: int | str, to: str | int, create: bool = False, dry_run: bool = False
) -> dict:
    return await move_chat(c, chat, to, create=create, dry=dry_run)


# --- MCP server --------------------------------------------------------------


MCP_INSTRUCTIONS = (
    "Telegram user-account tools (MTProto, Telethon). Chat ids are marked peer "
    "ids from list_chats (users positive, groups negative, channels -100...); "
    "pass them back verbatim. Writes act on the real account immediately and "
    "are visible to peers: send_message sends as you, react and mark_read are "
    "visible to the other side, archive_chat/create_folder/move_chat_to_folder "
    "change your folder state. move_chat_to_folder is additive; prefer "
    "dry_run=true to preview. Reading never marks chats as read. FloodWait is "
    "retried once automatically; avoid bursts of writes."
)


MCP_TOOLS = [
    ("me", "Logged-in Telegram user: id, name, username, phone.", me_row, True),
    (
        "list_chats",
        "All dialogs with type, unread count, folder memberships, archive "
        "state and last message. Filter by folder title.",
        chats_rows,
        True,
    ),
    (
        "get_chat_history",
        "Messages of a chat, oldest first, full text. Paginate with before_id "
        "(return messages older than this id). Does NOT mark the chat as read.",
        history_rows,
        True,
    ),
    (
        "search_messages",
        "Search messages by query, globally or within one chat.",
        search_rows,
        True,
    ),
    ("list_contacts", "Contact list of the account.", contacts_rows, True),
    ("list_folders", "Folders (dialog filters) with member chat ids.", folders_rows, True),
    (
        "download_media",
        "Download the media of a message to ~/.cache/tg2llm/media, return the "
        "local path.",
        download_media,
        True,
    ),
    (
        "send_message",
        "Send a message as the logged-in user. REAL send, visible to the peer. "
        "Optionally reply to a message id and attach a file path.",
        send_message,
        False,
    ),
    (
        "react",
        "Set an emoji reaction on a message, or clear it with an empty "
        "string. Visible to peers.",
        react,
        False,
    ),
    (
        "mark_read",
        "Mark a chat as read. The read receipt IS visible to the peer.",
        mark_read,
        False,
    ),
    ("archive_chat", "Archive a chat, or unarchive with undo=true.", archive_chat, False),
    (
        "create_folder",
        "Create a folder (title <= 12 chars, max 10), optionally seeded with "
        "one chat.",
        create_folder,
        False,
    ),
    (
        "move_chat_to_folder",
        "Add a chat to a folder, additively. dry_run=true previews without "
        "sending anything.",
        move_to_folder,
        False,
    ),
]

_MCP_CLIENT = None
_MCP_LOCK = asyncio.Lock()


async def mcp_client():
    global _MCP_CLIENT
    async with _MCP_LOCK:
        if _MCP_CLIENT is None:
            c = make_client()
            await c.connect()
            await must_auth(c)
            _MCP_CLIENT = c
        return _MCP_CLIENT


def bind_tool(fn):
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())[1:]

    async def bound(**kwargs):
        from mcp.server.mcpserver.exceptions import ToolError

        try:
            res = await fn(await mcp_client(), **kwargs)
        except CliError as e:
            raise ToolError(str(e)) from e
        # single content item + proper structured output for list results
        return {"rows": res} if isinstance(res, list) else res
    bound.__signature__ = inspect.Signature(params, return_annotation=dict)

    return bound


def build_mcp_server():
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer(name="tg", instructions=MCP_INSTRUCTIONS)
    for name, description, fn, read_only in MCP_TOOLS:
        server.add_tool(
            bind_tool(fn),
            name=name,
            description=description,
            annotations=ToolAnnotations(read_only_hint=read_only),
        )
    return server


async def cmd_mcp(_):
    server = build_mcp_server()
    try:
        await server.run_stdio_async()
    finally:
        if _MCP_CLIENT is not None:
            await _MCP_CLIENT.disconnect()



# --- command handlers -------------------------------------------------------


async def cmd_auth(_):
    c = make_client()
    await c.start()
    me = await c.get_me()
    out({"user": me.id, "name": (f"{me.first_name or ''} {me.last_name or ''}").strip()})
    await c.disconnect()


async def cmd_contacts(_):
    async with session() as c:
        await must_auth(c)
        out(await contacts_rows(c))


async def cmd_chats(args):
    async with session() as c:
        await must_auth(c)
        out(await chats_rows(c, folder=args.folder, limit=args.limit))


async def cmd_read(args):
    async with session() as c:
        await must_auth(c)
        msgs = await c.get_messages(parse_chat(args.chat), limit=args.last)
        out(
            [
                {
                    "id": m.id,
                    "date": m.date,
                    "sender_id": m.sender_id,
                    "text": (m.raw_text or "")[:200],
                }
                for m in reversed(msgs)
            ]
        )


async def cmd_folders(_):
    async with session() as c:
        await must_auth(c)
        out(await folders_rows(c))


async def cmd_create_folder(args):
    async with session() as c:
        await must_auth(c)
        out(await create_folder(c, args.title, chat=args.chat))


async def cmd_move(args):
    async with session() as c:
        await must_auth(c)
        out(
            await move_chat(
                c, args.chat, args.to, create=args.create, dry=args.dry_run
            )
        )


async def cmd_prime(_):
    print(PRIME)


async def cmd_archive(args):
    async with session() as c:
        await must_auth(c)
        out(await archive_chat(c, args.chat, undo=args.undo))


# --- CLI --------------------------------------------------------------------


def main(argv=None):
    p = argparse.ArgumentParser(prog="tg", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="one-time interactive login")

    sub.add_parser("prime", help="print the agent skill (how to use this CLI)")

    sub.add_parser("contacts", help="list contacts")

    sp = sub.add_parser("chats", help="list dialogs + folder memberships")
    sp.add_argument("--folder", help="only chats in this folder (title)")
    sp.add_argument("--limit", type=int, default=None)

    sp = sub.add_parser("read", help="recent messages of a chat")
    sp.add_argument("chat", help="chat id or @username")
    sp.add_argument("--last", type=int, default=20)

    sub.add_parser("folders", help="list folders")

    sp = sub.add_parser("create-folder", help="create a folder")
    sp.add_argument("title", help=f"<={MAX_TITLE} chars")
    sp.add_argument("--chat", help="seed member (id or @username)")

    sp = sub.add_parser("move", help="add chat to folder (additive)")
    sp.add_argument("chat")
    sp.add_argument("--to", required=True, help="folder title or id")
    sp.add_argument("--create", action="store_true", help="create folder if missing")
    sp.add_argument("--dry-run", action="store_true")

    sp = sub.add_parser("archive", help="archive a chat")
    sp.add_argument("chat")
    sp.add_argument("--undo", action="store_true", help="unarchive")

    sub.add_parser("mcp", help="run the MCP server on stdio")

    args = p.parse_args(argv)
    handler = {
        "auth": cmd_auth,
        "prime": cmd_prime,
        "contacts": cmd_contacts,
        "chats": cmd_chats,
        "read": cmd_read,
        "folders": cmd_folders,
        "create-folder": cmd_create_folder,
        "move": cmd_move,
        "archive": cmd_archive,
        "mcp": cmd_mcp,
    }[args.cmd]
    try:
        asyncio.run(handler(args))
    except CliError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
