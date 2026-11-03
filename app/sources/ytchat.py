"""Pembaca live chat YouTube tanpa API key (tidak resmi).

ChatDownloader.get_chat() (chat-downloader 0.2.8) tidak dipakai langsung: ia selalu membuka
`live_chat?continuation=...`, yang kini dikirim YouTube sebagai halaman kosong ("ParsingError: Unable to
parse initial video data"). Modul ini memakai `live_chat?v=<id>` dan endpoint polling
`youtubei/v1/live_chat/get_live_chat`, tetapi tetap memakai sesi HTTP dan parser pesan chat-downloader
agar format dict-nya sama (message_type, author, money, header_primary_text, ...).
"""

from chat_downloader.sites.youtube import YouTubeChatDownloader
from chat_downloader.utils.core import (
    camel_case_split,
    multi_get,
    regex_search,
    remove_prefixes,
    remove_suffixes,
    try_parse_json,
)

from .base import FatalSourceError

HOME = "https://www.youtube.com"
CHAT_PAGE = HOME + "/live_chat?is_popout=1&v={}"
POLL_URL = HOME + "/youtubei/v1/live_chat/get_live_chat"


# Moderator menghapus satu pesan / semua pesan seorang penonton (ban atau timeout).
DELETE_ACTIONS = ("markChatItemAsDeletedAction", "removeChatItemAction")
AUTHOR_DELETE_ACTIONS = ("markChatItemsByAuthorAsDeletedAction", "removeChatItemByAuthorAction")


def runs_to_parts(text_obj) -> list[dict]:
    """Isi pesan YouTube (runs) -> [{"t": teks} | {"img": url, "alt": ":emoji:"}].
    Emoji standar berupa karakter Unicode; hanya emoji kustom channel yang berupa gambar."""
    if not isinstance(text_obj, dict):
        return []
    if "simpleText" in text_obj:
        return [{"t": str(text_obj["simpleText"])}]
    parts = []
    for run in text_obj.get("runs") or []:
        if "text" in run:
            parts.append({"t": str(run["text"])})
        elif "emoji" in run:
            e = run["emoji"]
            alt = multi_get(e, "shortcuts", 0) or e.get("emojiId") or ""
            if e.get("isCustomEmoji"):
                thumbs = multi_get(e, "image", "thumbnails") or []
                url = thumbs[-1].get("url", "") if thumbs else ""
                parts.append({"img": url, "alt": alt} if url else {"t": alt})
            else:
                parts.append({"t": e.get("emojiId") or alt})
    return parts


def to_message(action: dict):
    """Satu action chat -> dict pesan gaya chat-downloader, atau None.
    Penghapusan oleh moderator menjadi message_type "deleted" / "author_deleted"."""
    for key in DELETE_ACTIONS:
        if key in action:
            target = (action[key] or {}).get("targetItemId")
            return {"message_type": "deleted", "target_id": target} if target else None
    for key in AUTHOR_DELETE_ACTIONS:
        if key in action:
            channel = (action[key] or {}).get("externalChannelId")
            return {"message_type": "author_deleted", "channel_id": channel} if channel else None
    item = multi_get(action, "addChatItemAction", "item")
    if not item:
        return None
    renderer = next(iter(item))
    data = YouTubeChatDownloader._parse_item(item, {}, 0)
    data["message_type"] = camel_case_split(remove_suffixes(remove_prefixes(renderer, "liveChat"), "Renderer"))
    data["parts"] = runs_to_parts((item.get(renderer) or {}).get("message"))
    return data


def next_continuation(container: dict):
    """(token, jeda_detik) dari daftar 'continuations' YouTube."""
    for c in container.get("continuations") or []:
        for value in c.values():
            if isinstance(value, dict) and value.get("continuation"):
                return value["continuation"], (value.get("timeoutMs") or 5000) / 1000
    return None, 5.0


class LiveChatReader:
    def __init__(self, video_id: str):
        self.video_id = video_id
        self.yt = YouTubeChatDownloader()

    def open(self):
        """Ambil pesan awal, token polling, dan konfigurasi. Error di sini berarti belum tersambung."""
        self._state = self._open()

    def _open(self):
        r = self.yt._session_get(CHAT_PAGE.format(self.video_id))
        html = r.text
        if r.status_code == 404:
            raise FatalSourceError("Video tidak ditemukan. Periksa URL live.")
        data = try_parse_json(regex_search(html, YouTubeChatDownloader._YT_INITIAL_DATA_RE))
        if not data:
            raise ConnectionError("Halaman chat YouTube tidak berisi data (biasanya sementara)")
        cfg = try_parse_json(regex_search(html, YouTubeChatDownloader._YT_CFG_RE), {})
        chat = multi_get(data, "contents", "liveChatRenderer")
        if not chat:
            note = multi_get(data, "contents", "messageRenderer", "text", "runs", 0, "text") or "chat tidak tersedia"
            raise FatalSourceError(
                f"Live chat tidak bisa dibaca: {note} (live belum mulai / sudah selesai / chat dimatikan)"
            )
        token, _ = next_continuation(chat)
        # Menu tampilan chat: [0] "Top chat" (terfilter), [1] "Live chat" (semua pesan). Pakai Live chat.
        menu = ("header", "liveChatHeaderRenderer", "viewSelector", "sortFilterSubMenuRenderer", "subMenuItems")
        views = multi_get(chat, *menu) or []
        if len(views) >= 2:
            token = multi_get(views[1], "continuation", "reloadContinuationData", "continuation") or token
        if not token:
            raise ConnectionError("Token chat YouTube tidak ditemukan")
        return chat.get("actions") or [], token, cfg

    def messages(self, stop):
        """Hasilkan pesan sampai stop di-set atau chat berakhir. Panggil open() dulu."""
        actions, token, cfg = self._state
        for a in actions:
            msg = to_message(a)
            if msg:
                yield msg
        context = cfg.get("INNERTUBE_CONTEXT") or {}
        key = cfg.get("INNERTUBE_API_KEY")
        url = POLL_URL + (f"?key={key}" if key else "")
        while not stop.is_set():
            body = {"context": context, "continuation": token}
            info = self.yt._get_continuation_info(url, {"max_attempts": 3}, json=body)
            if "error" in info:
                raise ConnectionError(f"YouTube menolak permintaan chat: {multi_get(info, 'error', 'message')}")
            cont = multi_get(info, "continuationContents", "liveChatContinuation")
            if not cont:
                return  # live chat sudah berakhir
            for a in cont.get("actions") or []:
                msg = to_message(a)
                if msg:
                    yield msg
            token, wait = next_continuation(cont)
            if not token:
                return
            if stop.wait(min(max(wait, 1.0), 10.0)):
                return
