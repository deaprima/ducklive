from fastapi.testclient import TestClient

from app.main import create_app
from app.sources.api import api_chat_op
from app.sources.chat import MAX_TEXT, clip_parts, make_item
from app.sources.scrape import chat_op
from app.sources.ytchat import runs_to_parts, to_message

TOKEN = "t0ken-untuk-test-chat"
H = {"X-Admin-Token": TOKEN}
WS_HOST = {"host": "127.0.0.1:8765"}


def text_action(msg_id, runs, badges=None):
    renderer = {
        "id": msg_id,
        "authorName": {"simpleText": "Sari"},
        "authorExternalChannelId": "UC_sari",
        "message": {"runs": runs},
    }
    if badges:
        renderer["authorBadges"] = badges
    return {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": renderer}}}


def test_runs_to_parts_keeps_unicode_and_custom_emoji():
    parts = runs_to_parts(
        {
            "runs": [
                {"text": "halo "},
                {"emoji": {"emojiId": "😀", "shortcuts": [":grinning:"]}},
                {
                    "emoji": {
                        "emojiId": "UC/abc",
                        "shortcuts": [":duck:"],
                        "isCustomEmoji": True,
                        "image": {"thumbnails": [{"url": "https://yt3.ggpht.com/a=s24"}]},
                    }
                },
            ]
        }
    )
    assert parts == [{"t": "halo "}, {"t": "😀"}, {"img": "https://yt3.ggpht.com/a=s24", "alt": ":duck:"}]
    assert runs_to_parts({"simpleText": "x"}) == [{"t": "x"}]
    assert runs_to_parts(None) == []


def test_scrape_text_message_to_chat_item():
    owner = [{"liveChatAuthorBadgeRenderer": {"icon": {"iconType": "OWNER"}, "tooltip": "Owner"}}]
    op = chat_op(to_message(text_action("m1", [{"text": "halo"}], owner)))
    assert op["op"] == "add"
    item = op["item"]
    assert (item["id"], item["kind"], item["author"], item["channel"], item["role"]) == (
        "m1",
        "text",
        "Sari",
        "UC_sari",
        "owner",
    )
    assert item["parts"] == [{"t": "halo"}]


def test_scrape_moderation_actions():
    assert chat_op(to_message({"markChatItemAsDeletedAction": {"targetItemId": "m1"}})) == {"op": "delete", "id": "m1"}
    assert chat_op(to_message({"markChatItemsByAuthorAsDeletedAction": {"externalChannelId": "UC_x"}})) == {
        "op": "ban",
        "channel": "UC_x",
    }
    assert to_message({"markChatItemAsDeletedAction": {}}) is None


def test_scrape_paid_and_gift_items():
    sc = chat_op({"message_type": "paid_message", "message_id": "p1", "money": {"text": "Rp50.000"}, "parts": []})
    assert sc["item"]["kind"] == "superchat" and sc["item"]["amount"] == "Rp50.000"
    gift = chat_op(
        {
            "message_type": "sponsorships_gift_purchase_announcement",
            "message_id": "g1",
            "header_primary_text": "Sent 5 Channel memberships",
        }
    )
    assert gift["item"]["parts"] == [{"t": "Gift 5 membership"}]
    # pesan tanpa id dan jenis yang tidak ditampilkan diabaikan
    assert chat_op({"message_type": "text_message"}) is None
    assert chat_op({"message_type": "viewer_engagement_message", "message_id": "x"}) is None


def test_clip_parts_limits_text_and_rejects_foreign_images():
    parts = clip_parts(
        [
            {"t": "a" * (MAX_TEXT + 50)},
            {"img": "https://evil.example/x.png", "alt": "x"},
            {"img": "javascript:alert(1)"},
            {"t": "tidak muat"},
        ]
    )
    assert parts == [{"t": "a" * MAX_TEXT}]
    assert make_item("1", "text", "A", role="admin")["role"] == ""


def test_api_chat_ops():
    item = {
        "id": "a1",
        "snippet": {"type": "textMessageEvent", "displayMessage": "halo"},
        "authorDetails": {"displayName": "Budi", "channelId": "UC_b", "isChatModerator": True},
    }
    op = api_chat_op(item)
    assert op["item"]["parts"] == [{"t": "halo"}] and op["item"]["role"] == "moderator"
    sc = api_chat_op(
        {
            "id": "a2",
            "snippet": {
                "type": "superChatEvent",
                "displayMessage": "Rp50.000 from Budi: gas",
                "superChatDetails": {"userComment": "gas", "amountDisplayString": "Rp50.000"},
            },
            "authorDetails": {"displayName": "Budi"},
        }
    )
    assert sc["item"]["parts"] == [{"t": "gas"}] and sc["item"]["amount"] == "Rp50.000"
    deleted = {"snippet": {"type": "messageDeletedEvent", "messageDeletedDetails": {"deletedMessageId": "a1"}}}
    assert api_chat_op(deleted) == {"op": "delete", "id": "a1"}
    ban = {"snippet": {"type": "userBannedEvent", "userBannedDetails": {"bannedUserDetails": {"channelId": "UC_b"}}}}
    assert api_chat_op(ban) == {"op": "ban", "channel": "UC_b"}
    assert api_chat_op({"id": "z", "snippet": {"type": "pollEvent"}}) is None


def add(app, msg_id, channel="UC_a", text="hai"):
    app.state.chat({"op": "add", "item": make_item(msg_id, "text", "A", channel, "", [{"t": text}])})


def receive_type(ws, wanted, limit=10):
    for _ in range(limit):
        m = ws.receive_json()
        if m["type"] == wanted:
            return m
    raise AssertionError(f"tidak menerima {wanted}")


def test_chat_history_dedupe_and_moderation_over_ws(tmp_path):
    app = create_app(db_file=tmp_path / "a.db", admin_token=TOKEN)
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    with c:
        add(app, "m1")
        add(app, "m1")  # sambung ulang: pesan yang sama tidak boleh tampil dua kali
        add(app, "m2", channel="UC_spam")
        with c.websocket_connect("/ws", headers=WS_HOST) as ws:
            history = receive_type(ws, "chat_history")["items"]
            assert [x["id"] for x in history] == ["m1", "m2"]
            add(app, "m3")
            assert receive_type(ws, "chat")["item"]["id"] == "m3"
            app.state.chat({"op": "delete", "id": "m1"})
            assert receive_type(ws, "chat_delete") == {"type": "chat_delete", "id": "m1"}
            app.state.chat({"op": "ban", "channel": "UC_spam"})
            assert receive_type(ws, "chat_delete")["channel"] == "UC_spam"
        with c.websocket_connect("/ws", headers=WS_HOST) as ws:
            assert [x["id"] for x in receive_type(ws, "chat_history")["items"]] == ["m3"]


def test_chat_test_endpoint_and_overlay(tmp_path):
    app = create_app(db_file=tmp_path / "a.db", admin_token=TOKEN)
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    with c:
        assert c.post("/api/chat/test").status_code == 401
        assert c.post("/api/chat/test", headers=H).status_code == 200
        assert c.get("/overlay/chat").status_code == 200
        c.post("/api/settings", headers=H, json={"chat_max_items": 999})
        s = c.get("/api/settings", headers=H).json()
        assert s["chat_max_items"] == 15 and s["chat_hide_links"] is True
        assert "chat_fade_seconds" not in s
        assert s["chat_show_owner"] is False  # chat host sendiri tidak tampil secara bawaan


def test_avatar_and_member_badge_only_from_youtube_hosts():
    msg = {
        "message_type": "text_message",
        "message_id": "b1",
        "parts": [{"t": "hai"}],
        "author": {
            "name": "Sari",
            "images": [
                {"url": "https://yt4.ggpht.com/a=s32", "width": 32},
                {"url": "https://yt4.ggpht.com/a=s64", "width": 64},
            ],
            "badges": [{"title": "Member (6 months)", "icons": [{"url": "https://yt3.ggpht.com/b=s16", "width": 16}]}],
        },
    }
    item = chat_op(msg)["item"]
    assert item["avatar"] == "https://yt4.ggpht.com/a=s64"
    assert item["badge"] == "https://yt3.ggpht.com/b=s16"
    assert item["role"] == "member"
    evil = make_item("x", "text", "A", avatar="https://evil.example/a.png", badge="javascript:alert(1)")
    assert evil["avatar"] == "" and evil["badge"] == ""
    api = api_chat_op(
        {
            "id": "a9",
            "snippet": {"type": "textMessageEvent", "displayMessage": "hai"},
            "authorDetails": {"displayName": "B", "profileImageUrl": "https://yt3.ggpht.com/p.jpg"},
        }
    )
    assert api["item"]["avatar"] == "https://yt3.ggpht.com/p.jpg"


def test_broken_chat_format_never_stops_event_counting():
    from app.sources.base import Source

    events = []
    src = Source(lambda *a: events.append(a), lambda: {}, chat=lambda op: None)
    src.forward_chat(lambda raw: raw["tidak-ada"], {})  # format chat tak dikenal
    src.forward_chat(chat_op, {"message_type": "text_message", "message_id": "x", "parts": ["bukan dict"]})
    assert src.chat_errors == 1
    assert clip_parts(["bukan dict", {"t": "ok"}]) == [{"t": "ok"}]
    assert len(make_item("1", "text", "A", channel="U" * 500)["channel"]) == 100
