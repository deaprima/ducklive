from app.sources.api import parse_item, video_id_from
from app.sources.scrape import parse_message


def test_scrape_parse():
    assert parse_message({"message_type": "membership_item"}) == ("member", 1, None, None)
    assert parse_message(
        {"message_type": "sponsorships_gift_purchase_announcement", "message": "Sent 5 Channel memberships"}
    ) == ("gift", 5, None, None)
    assert (
        parse_message({"message_type": "sponsorships_gift_purchase_announcement", "message": "Sent 1.000 gifts"})[1]
        == 1000
    )
    assert parse_message({"message_type": "sponsorships_gift_purchase_announcement"})[1] == 1
    # angka di dalam nama channel tidak boleh terbaca sebagai jumlah gift
    gift = {"message_type": "sponsorships_gift_purchase_announcement"}
    assert parse_message({**gift, "header_primary_text": "Sent 5 Gamer2 memberships"})[1] == 5
    assert parse_message({**gift, "header_primary_text": "Gifted 20 Studio21 memberships", "message": "99"})[1] == 20
    assert parse_message({**gift, "header_primary_text": "Mengirim 3 langganan Top10Gaming"})[1] == 3
    assert parse_message({**gift, "header_primary_text": "Sent Gamer2 memberships"})[1] == 1
    assert parse_message({"message_type": "paid_message", "money": {"amount": 25000, "currency": "IDR"}}) == (
        "superchat",
        0,
        25000.0,
        "IDR",
    )
    assert parse_message({"message_type": "text_message"}) is None


def test_api_parse():
    assert parse_item({"snippet": {"type": "newSponsorEvent"}}) == ("member", 1, None, None)
    assert (
        parse_item(
            {"snippet": {"type": "membershipGiftingEvent", "membershipGiftingDetails": {"giftMembershipsCount": 7}}}
        )[1]
        == 7
    )
    assert parse_item({"snippet": {"type": "giftMembershipReceivedEvent"}}) is None
    assert parse_item({"snippet": {"type": "memberMilestoneChatEvent"}}) is None
    assert parse_item(
        {"snippet": {"type": "superChatEvent", "superChatDetails": {"amountMicros": "25000000000", "currency": "IDR"}}}
    ) == ("superchat", 0, 25000.0, "IDR")


def test_video_id():
    assert video_id_from("https://www.youtube.com/watch?v=abcdefghijk&t=3") == "abcdefghijk"
    assert video_id_from("https://youtu.be/abcdefghijk") == "abcdefghijk"
    assert video_id_from("https://www.youtube.com/live/abcdefghijk?si=1") == "abcdefghijk"


def test_ytchat_actions_to_messages():
    from app.sources.ytchat import next_continuation, to_message

    member = to_message(
        {
            "addChatItemAction": {
                "item": {"liveChatMembershipItemRenderer": {"id": "m1", "authorName": {"simpleText": "Budi"}}}
            }
        }
    )
    assert member["message_type"] == "membership_item" and member["message_id"] == "m1"
    assert member["author"]["name"] == "Budi"
    assert parse_message(member) == ("member", 1, None, None)

    gift = to_message(
        {
            "addChatItemAction": {
                "item": {
                    "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer": {
                        "id": "g1",
                        "header": {
                            "liveChatSponsorshipsHeaderRenderer": {
                                "authorName": {"simpleText": "Sari"},
                                "primaryText": {
                                    "runs": [{"text": "Sent "}, {"text": "5"}, {"text": " Gamer2 memberships"}]
                                },
                            }
                        },
                    }
                }
            }
        }
    )
    assert gift["message_type"] == "sponsorships_gift_purchase_announcement"
    assert parse_message(gift) == ("gift", 5, None, None)

    # Hadiah Jewels (Gold coin, Star) bukan gift membership: tidak dihitung
    jewel = to_message(
        {"addChatItemAction": {"item": {"giftMessageViewModel": {"id": "j1", "text": {"content": "sent Star"}}}}}
    )
    assert parse_message(jewel) is None
    assert to_message({"removeChatItemAction": {"targetItemId": "x"}}) is None

    assert next_continuation(
        {"continuations": [{"invalidationContinuationData": {"continuation": "T", "timeoutMs": 10000}}]}
    ) == ("T", 10.0)
    assert next_continuation({}) == (None, 5.0)
