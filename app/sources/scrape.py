"""Sumber tanpa API key: membaca live chat publik (tidak resmi, bisa rusak jika YouTube berubah)."""

import re

from .api import video_id_from
from .base import FatalSourceError, Source
from .chat import make_item, pick_image

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
# Angka yang berdiri sendiri (bukan bagian dari nama seperti "Gamer2"), boleh dengan pemisah ribuan "1.000".
NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:[.,]\d{3})+|\d+)(?![\w])")


def gift_count(msg: dict) -> int:
    """Jumlah gift dari header, mis. "Sent 5 <channel> memberships". Default 1."""
    for key in ("header_primary_text", "message"):
        m = NUMBER.search(str(msg.get(key) or ""))
        if m:
            n = int(re.sub(r"[.,]", "", m.group(1)))
            if n > 0:
                return n
    return 1


def parse_message(msg: dict):
    """Ubah satu pesan chat menjadi (kind, units, amount, currency) atau None.
    Hanya pembelian gift yang dihitung; pesan "penerima gift" diabaikan agar tidak dobel."""
    mtype = msg.get("message_type")
    if mtype == "membership_item":
        return "member", 1, None, None
    if mtype == "sponsorships_gift_purchase_announcement":
        return "gift", gift_count(msg), None, None
    if mtype in ("paid_message", "paid_sticker"):
        money = msg.get("money") or {}
        try:
            amount = float(money.get("amount"))
        except (TypeError, ValueError):
            return None
        return "superchat", 0, amount, money.get("currency")
    return None


CHAT_KINDS = {
    "text_message": "text",
    "paid_message": "superchat",
    "paid_sticker": "superchat",
    "membership_item": "member",
    "sponsorships_gift_purchase_announcement": "gift",
}


def author_role(author: dict) -> str:
    badges = author.get("badges") or []
    icons = {b.get("icon_name") for b in badges}
    if "owner" in icons:
        return "owner"
    if "moderator" in icons:
        return "moderator"
    if any(b.get("icons") for b in badges):  # lencana member berupa gambar kustom channel
        return "member"
    return "verified" if "verified" in icons else ""


def chat_op(msg: dict):
    """Pesan chat -> operasi untuk overlay chat (lihat sources/chat.py), atau None."""
    mtype = msg.get("message_type")
    if mtype == "deleted":
        return {"op": "delete", "id": msg["target_id"]}
    if mtype == "author_deleted":
        return {"op": "ban", "channel": msg["channel_id"]}
    kind = CHAT_KINDS.get(mtype)
    if not kind or not msg.get("message_id"):
        return None
    author = msg.get("author") or {}
    parts = msg.get("parts") or []
    if kind == "gift":
        parts = [{"t": f"Gift {gift_count(msg)} membership"}]
    elif kind == "member" and not parts:
        parts = [{"t": msg.get("header_secondary_text") or "Member baru"}]
    amount = (msg.get("money") or {}).get("text") if kind == "superchat" else None
    badge = next((pick_image(b["icons"], 32) for b in author.get("badges") or [] if b.get("icons")), "")
    item = make_item(
        msg["message_id"],
        kind,
        author.get("name"),
        author.get("id"),
        author_role(author),
        parts,
        amount,
        avatar=pick_image(author.get("images")),
        badge=badge,
    )
    return {"op": "add", "item": item}


class ScrapeSource(Source):
    name = "scrape"

    def run(self):
        from .ytchat import LiveChatReader  # impor lambat: chat-downloader berat

        url = self.get_settings().get("video_url", "").strip()
        if not url:
            raise FatalSourceError("URL live belum diisi")
        vid = video_id_from(url)
        if not VIDEO_ID.fullmatch(vid):
            raise FatalSourceError("URL live tidak dikenali. Contoh: https://www.youtube.com/watch?v=XXXXXXXXXXX")
        reader = LiveChatReader(vid)
        reader.open()
        self.connected()
        self.info = "terhubung ke chat"
        for msg in reader.messages(self._stop):
            if self._stop.is_set():
                break
            self.forward_chat(chat_op, msg)
            parsed = parse_message(msg)
            if not parsed:
                continue
            kind, units, amount, currency = parsed
            author = (msg.get("author") or {}).get("name", "")
            self.emit(msg.get("message_id"), kind, author, units, amount, currency)
        if not self._stop.is_set():
            # Chat berakhir = live selesai. Tidak disambung ulang agar tidak membaca chat replay.
            self.info = "chat berakhir (live selesai?) — klik Sambungkan lagi jika live masih jalan"
            self.notify("Live chat berakhir. Jika live masih berjalan, klik Sambungkan lagi.")
        else:
            self.info = "berhenti"
