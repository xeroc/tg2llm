import asyncio

from telethon import functions, types, utils

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

    async def get_entity(self, chat_id):
        return types.User(id=42, access_hash=7)

    async def __call__(self, request):
        self.calls.append(request)
        return self.filters


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
    res = run(tg.move_chat(c, 42, "news"))
    assert res["action"] == "added"
    assert len(updates(c)) == 1
    assert utils.get_peer_id(f.include_peers[1]) == 42
    assert tg.filter_title(f) == "News"  # untouched


def test_move_already_member_no_call():
    f = mkfilter(5, types.TextPlain("News"), include=[IPU(user_id=42, access_hash=7)])
    c = FakeClient([f])
    res = run(tg.move_chat(c, 42, "news"))
    assert res["action"] == "already"
    assert updates(c) == []


def test_move_dry_run_no_call():
    f = mkfilter(5, types.TextPlain("News"))
    c = FakeClient([f])
    res = run(tg.move_chat(c, 42, "news", dry=True))
    assert res["action"] == "added" and res["dry_run"] is True
    assert updates(c) == []


def test_move_creates_folder_with_flag():
    c = FakeClient([])
    res = run(tg.move_chat(c, 42, "work", create=True))
    assert res["action"] == "created+added"
    assert len(updates(c)) == 1
    sent = updates(c)[0]
    assert sent.id == 2 and len(sent.filter.include_peers) == 1  # type: ignore[union-attr]


def test_move_missing_folder_raises():
    c = FakeClient([])
    try:
        run(tg.move_chat(c, 42, "work"))
        raise AssertionError("expected CliError")
    except tg.CliError:
        pass


def test_new_folder_title_limit():
    try:
        tg.new_filter(3, "x" * 13)
        raise AssertionError("expected CliError")
    except tg.CliError:
        pass
