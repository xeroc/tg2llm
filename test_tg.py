import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from telethon import functions, types

import tg

IPU = types.InputPeerUser


def mkfilter(fid, title, include=()):
    return types.DialogFilter(
        id=fid,
        title=title,
        pinned_peers=[],
        include_peers=list(include),
        exclude_peers=[],
    )


class FakeClient:
    def __init__(self, filters):
        self.filters = filters
        self.calls = []
        self.result = filters
        self.entity = types.User(id=42, access_hash=7)
        self.dialogs = []
        self.messages = []
        self.msg_kwargs = {}
        self.iter_searches = []
        self.sent = []
        self.downloads = []

    async def get_entity(self, chat_id):
        return self.entity

    async def __call__(self, request):
        self.calls.append(request)
        return self.result

    async def get_me(self):
        return types.User(
            id=42,
            access_hash=9,
            first_name="Ada",
            last_name="Byron",
            username="ada",
            phone="49150",
        )

    async def get_messages(self, chat, limit=20, offset_id=0, ids=None):
        self.msg_kwargs = {"limit": limit, "offset_id": offset_id, "ids": ids}
        if ids is not None:
            return [self.messages[0] if self.messages else None]
        return list(reversed(self.messages))[:limit]

    async def send_message(self, entity, message=None, reply_to=None, file=None):
        self.sent.append(
            {"entity": entity, "message": message, "reply_to": reply_to, "file": file}
        )
        return SimpleNamespace(id=555)

    async def download_media(self, message, file=None):
        self.downloads.append((message.id, file))
        return "/cache/media_1.jpg"

    async def iter_dialogs(self, limit=None):
        for d in self.dialogs[:limit] if limit else self.dialogs:
            yield d

    async def iter_messages(self, entity, search=None, limit=None):
        self.iter_searches.append({"search": search, "limit": limit})
        for m in self.messages[:limit] if limit else self.messages:
            yield m


def run(coro):
    return asyncio.run(coro)


def updates(c):
    return [
        r
        for r in c.calls
        if isinstance(r, functions.messages.UpdateDialogFilterRequest)
    ]


# --- unwrap_filters ---------------------------------------------------------


def test_unwrap_filters_vector():
    fs = [mkfilter(2, types.TextPlain("a")), types.DialogFilterDefault()]
    assert tg.unwrap_filters(fs) == fs


def test_unwrap_filters_wrapper():
    class W:
        filters = ["x"]

    assert tg.unwrap_filters(W()) == ["x"]


# --- titles / lookup --------------------------------------------------------


def test_filter_title_text_with_entities():
    assert tg.filter_title(mkfilter(2, types.TextPlain("News"))) == "News"


def test_filter_title_plain_str_fallback():
    assert tg.filter_title(mkfilter(2, "Raw")) == "Raw"


def test_find_filter_by_name_case_insensitive():
    fs = [types.DialogFilterDefault(), mkfilter(5, types.TextPlain("News"))]
    assert tg.find_filter(fs, "news").id == 5  # type: ignore[union-attr]


def test_find_filter_by_id_string():
    fs = [mkfilter(5, types.TextPlain("News"))]
    assert tg.find_filter(fs, "5").id == 5  # type: ignore[union-attr]


def test_find_filter_missing_returns_none():
    assert tg.find_filter([mkfilter(5, types.TextPlain("News"))], "work") is None


def test_next_filter_id():
    assert tg.next_filter_id([]) == 2
    assert tg.next_filter_id([mkfilter(2, "a"), mkfilter(5, "b")]) == 6


# --- membership / move ------------------------------------------------------


def test_folder_membership_map():
    fs = [mkfilter(5, types.TextPlain("News"), include=[IPU(user_id=1, access_hash=0)])]
    mem = tg.folder_membership(fs)
    assert mem[1] == ["News"]


def test_move_adds_peer_additively():
    f = mkfilter(5, types.TextPlain("News"), include=[IPU(user_id=1, access_hash=0)])
    c = FakeClient([f])
    run(tg.move_chat(c, 42, "news"))
    sent = updates(c)[0]
    assert sent.id == 5
    assert len(sent.filter.include_peers) == 2  # type: ignore[union-attr]
    assert tg.filter_title(f) == "News"  # untouched


def test_move_already_member_no_call():
    f = mkfilter(5, types.TextPlain("News"), include=[IPU(user_id=42, access_hash=7)])
    c = FakeClient([f])
    run(tg.move_chat(c, 42, "news"))
    assert updates(c) == []


def test_move_dry_run_no_call():
    f = mkfilter(5, types.TextPlain("News"))
    c = FakeClient([f])
    res = run(tg.move_chat(c, 42, "news", dry=True))
    assert res["dry_run"] is True
    assert updates(c) == []


def test_move_creates_folder_with_flag():
    c = FakeClient([])
    res = run(tg.move_chat(c, 42, "NewFolder", create=True))
    sent = updates(c)[0]
    assert sent.id == 2 and len(sent.filter.include_peers) == 1  # type: ignore[union-attr]
    assert res["action"] == "created+added"


def test_move_missing_folder_raises():
    c = FakeClient([])
    try:
        run(tg.move_chat(c, 42, "nope"))
        assert False
    except tg.CliError:
        pass


def test_new_folder_title_limit():
    try:
        tg.new_filter(3, "x" * 13)
        assert False
    except tg.CliError:
        pass


def test_new_filter_title_is_text_with_entities():
    f = tg.new_filter(3, "News")
    assert isinstance(f.title, types.TextWithEntities)
    assert f.title.text == "News"
    assert f.title.entities == []


# --- prime skill ------------------------------------------------------------


def test_prime_skill_safety_rules_present():
    low = tg.PRIME.lower()
    assert "never send a write command without human confirmation" in low
    assert "dry-run" in low and "explicit ok" in low
    for cmd in ("chats", "read", "contacts", "folders", "create-folder", "move", "archive"):
        assert cmd in tg.PRIME
    assert tg.PRIME.startswith("# tg")


# --- MCP tools ---------------------------------------------------------------


TOOL_NAMES = {
    "me",
    "list_chats",
    "get_chat_history",
    "search_messages",
    "list_contacts",
    "list_folders",
    "download_media",
    "send_message",
    "react",
    "mark_read",
    "archive_chat",
    "create_folder",
    "move_chat_to_folder",
}


def msg(i, text="hi", media=None):
    return SimpleNamespace(
        id=i, date=f"2026-09-0{i}", sender_id=7, raw_text=text, media=media
    )


def test_mcp_me():
    c = FakeClient([])
    assert run(tg.me_row(c)) == {
        "id": 42,
        "name": "Ada Byron",
        "username": "ada",
        "phone": "49150",
    }


def test_mcp_list_chats_rows_and_folder_filter():
    c = FakeClient(
        [mkfilter(5, types.TextPlain("Crypto"), include=[IPU(user_id=1, access_hash=0)])]
    )
    c.dialogs = [
        SimpleNamespace(
            id=1,
            name="Solana News",
            is_user=False,
            is_group=False,
            unread_count=3,
            archived=False,
            message=SimpleNamespace(date="d", raw_text="upgrade"),
        )
    ]
    rows = run(tg.chats_rows(c))
    assert rows[0]["folders"] == ["Crypto"]
    assert rows[0]["title"] == "Solana News"
    assert run(tg.chats_rows(c, folder="Crypto")) == rows
    assert run(tg.chats_rows(c, folder="Nope")) == []


def test_mcp_history_full_text_and_before_id():
    c = FakeClient([])
    long = "x" * 300
    c.messages = [msg(1, "a"), msg(2, long), msg(3, "c")]
    rows = run(tg.history_rows(c, -100123, limit=3, before_id=99))
    assert [r["id"] for r in rows] == [1, 2, 3]  # oldest first
    assert rows[1]["text"] == long  # untruncated
    assert c.msg_kwargs["offset_id"] == 99


def test_mcp_search_per_chat():
    c = FakeClient([])
    c.messages = [msg(1, "hello world"), msg(2, "nope")]
    run(tg.search_rows(c, "hello", chat=-100123, limit=5))
    assert c.iter_searches == [{"search": "hello", "limit": 5}]


def test_mcp_search_global():
    c = FakeClient([])
    m = msg(9, "found it")
    m.chat_id = -100123
    c.result = SimpleNamespace(messages=[m])
    rows = run(tg.search_rows(c, "found"))
    req = c.calls[0]
    assert isinstance(req, functions.messages.SearchGlobalRequest)
    assert req.q == "found"
    assert rows[0]["text"] == "found it"
    assert rows[0]["chat_id"] == -100123


def test_mcp_contacts():
    c = FakeClient([])
    c.result = SimpleNamespace(
        users=[
            types.User(
                id=1, first_name="Jane", last_name="Doe", username="jd", phone="49"
            )
        ]
    )
    rows = run(tg.contacts_rows(c))
    assert rows == [{"id": 1, "name": "Jane Doe", "username": "jd", "phone": "49"}]


def test_mcp_folders_rows():
    c = FakeClient(
        [mkfilter(5, types.TextPlain("Crypto"), include=[IPU(user_id=1, access_hash=0)])]
    )
    rows = run(tg.folders_rows(c))
    assert rows[0]["title"] == "Crypto"
    assert rows[0]["chats"] == [1]


def test_mcp_download_media():
    c = FakeClient([])
    c.messages = [msg(7, media=SimpleNamespace())]
    res = run(tg.download_media(c, -100123, 7))
    assert res == {"message_id": 7, "path": "/cache/media_1.jpg"}
    assert c.downloads == [(7, tg.MEDIA_DIR)]


def test_mcp_download_media_without_media_raises():
    c = FakeClient([])
    c.messages = [msg(7)]
    try:
        run(tg.download_media(c, -100123, 7))
        assert False
    except tg.CliError:
        pass


def test_mcp_send_message():
    c = FakeClient([])
    res = run(tg.send_message(c, "@ada", "hi there", reply_to=99))
    assert res == {"id": 555, "chat": 42}
    assert c.sent == [
        {"entity": c.entity, "message": "hi there", "reply_to": 99, "file": None}
    ]


def test_mcp_send_message_with_file():
    c = FakeClient([])
    run(tg.send_message(c, "@ada", "doc", file="/tmp/a.pdf"))
    assert c.sent[0]["file"] == "/tmp/a.pdf"


def test_mcp_react_sets_emoji():
    c = FakeClient([])
    res = run(tg.react(c, -100123, 4812, "👍"))
    req = c.calls[0]
    assert isinstance(req, functions.messages.SendReactionRequest)
    assert req.msg_id == 4812
    assert [r.emoticon for r in req.reaction] == ["👍"]
    assert res == {"chat": 42, "message_id": 4812, "reaction": "👍"}


def test_mcp_react_empty_clears():
    c = FakeClient([])
    run(tg.react(c, -100123, 4812, ""))
    assert c.calls[0].reaction == []


def test_mcp_mark_read_user():
    c = FakeClient([])
    run(tg.mark_read(c, "@ada"))
    req = c.calls[0]
    assert isinstance(req, functions.messages.ReadHistoryRequest)
    assert req.max_id == 0


def test_mcp_mark_read_channel():
    c = FakeClient([])
    c.entity = types.Channel(
        id=7, access_hash=1, title="News", photo=types.ChatPhotoEmpty(), date=0, megagroup=True
    )
    run(tg.mark_read(c, -1007))
    req = c.calls[0]
    assert isinstance(req, functions.channels.ReadHistoryRequest)
    assert req.max_id == 0


def test_mcp_archive_and_undo():
    c = FakeClient([])
    run(tg.archive_chat(c, "@ada"))
    run(tg.archive_chat(c, "@ada", undo=True))
    reqs = [
        r for r in c.calls if isinstance(r, functions.folders.EditPeerFoldersRequest)
    ]
    assert [r.folder_peers[0].folder_id for r in reqs] == [1, 0]


def test_mcp_create_folder():
    c = FakeClient([])
    res = run(tg.create_folder(c, "Crypto"))
    assert res == {"created": {"id": 2, "title": "Crypto"}}
    assert len(updates(c)) == 1


def test_mcp_move_dry_run_sends_nothing():
    c = FakeClient([mkfilter(5, types.TextPlain("Crypto"))])
    res = run(tg.move_to_folder(c, "@ada", "Crypto", dry_run=True))
    assert res["dry_run"] is True
    assert updates(c) == []


def test_mcp_server_stdio_roundtrip(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("TG_")}
    env["TG_SESSION"] = str(tmp_path / "s.session")
    env["TG_API_ID"] = ""
    env["TG_API_HASH"] = ""
    proc = subprocess.Popen(
        [sys.executable, str(Path(tg.__file__).parent / "tg.py"), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    reqs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "me", "arguments": {}},
        },
    ]
    # keep stdin open while reading: mcp shuts down on EOF and would cancel
    # the in-flight tools/call
    proc.stdin.write("".join(json.dumps(r) + "\n" for r in reqs))
    proc.stdin.flush()
    resp = {}
    for _ in range(3):
        line = proc.stdout.readline()
        if not line:
            break
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("id") in (1, 2, 3):
            resp[o["id"]] = o
    proc.stdin.close()
    proc.wait(timeout=10)
    assert {t["name"] for t in resp[2]["result"]["tools"]} == TOOL_NAMES
    assert "Telegram" in resp[1]["result"]["instructions"]
    r3 = resp[3]["result"]
    assert r3["isError"] is True
    assert "TG_API_ID" in r3["content"][0]["text"]


def test_mcp_client_init_is_singleton_under_concurrency(monkeypatch):
    import asyncio as aio

    class SlowFake:
        def __init__(self):
            self.n = 0

        async def connect(self):
            await aio.sleep(0.05)

        async def is_user_authorized(self):
            return True

    made = []

    def slow_make():
        made.append(SlowFake())
        return made[-1]

    monkeypatch.setattr(tg, "make_client", slow_make)
    monkeypatch.setattr(tg, "_MCP_CLIENT", None)

    async def two():
        return await aio.gather(tg.mcp_client(), tg.mcp_client())

    a, b = run(two())
    assert a is b
    assert len(made) == 1


def test_bind_tool_wraps_lists(monkeypatch):
    async def fake_client():
        return FakeClient([])

    monkeypatch.setattr(tg, "mcp_client", fake_client)

    bound = tg.bind_tool(tg.folders_rows)
    res = run(bound())
    assert res == {"rows": []}
