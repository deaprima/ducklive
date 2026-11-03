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


def to_message(action: dict):
    """Satu action 'addChatItemAction' -> dict pesan gaya chat-downloader, atau None."""
    item = multi_get(action, "addChatItemAction", "item")
    if not item:
        return None
    renderer = next(iter(item))
    data = YouTubeChatDownloader._parse_item(item, {}, 0)
    data["message_type"] = camel_case_split(remove_suffixes(remove_prefixes(renderer, "liveChat"), "Renderer"))
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
