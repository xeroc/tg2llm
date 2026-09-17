#!/usr/bin/env python3
"""tg - Telegram user-account CLI for agents. JSON out.

Env: TG_API_ID, TG_API_HASH (https://my.telegram.org), TG_SESSION (optional).
"""
import argparse
import asyncio
import contextlib
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
        title=types.TextPlain(title),
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
        raise CliError("set TG_API_ID and TG_API_HASH (from my.telegram.org)")
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
        res = await call(c, functions.contacts.GetContactsRequest(hash=0))
        out(
            [
                {
                    "id": u.id,
                    "name": " ".join(filter(None, [u.first_name, u.last_name])),
                    "username": u.username,
                    "phone": u.phone,
                }
                for u in getattr(res, "users", [])
            ]
        )


async def cmd_chats(args):
    async with session() as c:
        await must_auth(c)
        mem = folder_membership(await fetch_filters(c))
        rows = []
        async for d in c.iter_dialogs(limit=args.limit):
            rows.append(chat_row(d, mem))
        if args.folder:
            rows = [r for r in rows if args.folder in r["folders"]]
        out(rows)


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
        out(
            [
                {
                    "id": f.id,
                    "title": filter_title(f),
                    "emoticon": f.emoticon,
                    "chats": [utils.get_peer_id(p) for p in f.include_peers or []],
                }
                for f in custom_filters(await fetch_filters(c))
            ]
        )


async def cmd_create_folder(args):
    async with session() as c:
        await must_auth(c)
        filters = await fetch_filters(c)
        if len(custom_filters(filters)) >= MAX_FOLDERS:
            raise CliError(f"folder limit reached ({MAX_FOLDERS})")
        f = new_filter(next_filter_id(filters), args.title)
        if args.chat:
            ip = utils.get_input_peer(
                await c.get_entity(parse_chat(args.chat))
            )
            f.include_peers = [ip]
        await call(c, functions.messages.UpdateDialogFilterRequest(id=f.id, filter=f))
        out({"created": {"id": f.id, "title": filter_title(f)}})


async def cmd_move(args):
    async with session() as c:
        await must_auth(c)
        out(
            await move_chat(
                c, args.chat, args.to, create=args.create, dry=args.dry_run
            )
        )


async def cmd_archive(args):
    async with session() as c:
        await must_auth(c)
        ip = utils.get_input_peer(await c.get_entity(parse_chat(args.chat)))
        fid = 0 if args.undo else ARCHIVE_ID
        await call(
            c,
            functions.folders.EditPeerFoldersRequest(
                folder_peers=[types.InputFolderPeer(peer=ip, folder_id=fid)]
            ),
        )
        out({"chat": utils.get_peer_id(ip), "folder_id": fid})


# --- CLI --------------------------------------------------------------------


def main(argv=None):
    p = argparse.ArgumentParser(prog="tg", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="one-time interactive login")

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

    args = p.parse_args(argv)
    handler = {
        "auth": cmd_auth,
        "contacts": cmd_contacts,
        "chats": cmd_chats,
        "read": cmd_read,
        "folders": cmd_folders,
        "create-folder": cmd_create_folder,
        "move": cmd_move,
        "archive": cmd_archive,
    }[args.cmd]
    try:
        asyncio.run(handler(args))
    except CliError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
