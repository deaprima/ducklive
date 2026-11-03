"""Sumber resmi: YouTube Data API v3 (liveChatMessages.list). Membutuhkan OAuth pemilik channel."""

import os
import re
import time
from pathlib import Path

import httpx

from ..paths import data_dir
from .base import FatalSourceError, Source

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
API = "https://www.googleapis.com/youtube/v3"


def token_path() -> Path:
    return data_dir() / "google_token.json"


def client_secret_path() -> Path:
    return data_dir() / "client_secret.json"


def login_interactive():
    """Buka browser untuk login Google. Memblokir sampai selesai, jadi panggil dari thread terpisah."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    cs = client_secret_path()
    if not cs.exists():
        raise FileNotFoundError(f"Letakkan client_secret.json di {cs.parent}")
    flow = InstalledAppFlow.from_client_secrets_file(str(cs), SCOPES)
    creds = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True)
    tp = token_path()
    tp.write_text(creds.to_json())
    try:
        os.chmod(tp, 0o600)
    except OSError:
        pass
    return True


def load_credentials(force_refresh=False):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tp = token_path()
    if not tp.exists():
        raise FatalSourceError("Belum login Google. Klik 'Login Google' di Admin.")
    creds = Credentials.from_authorized_user_file(str(tp), SCOPES)
    if force_refresh or not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError:
            raise FatalSourceError("Login Google kedaluwarsa. Klik 'Login Google' lagi.") from None
        tp.write_text(creds.to_json())
    return creds


def video_id_from(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/live/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else url.strip()


def parse_item(item: dict):
    """Ubah satu item liveChatMessages menjadi (kind, units, amount, currency) atau None.
    giftMembershipReceivedEvent (penerima gift) sengaja diabaikan agar gift tidak terhitung dobel."""
    sn = item.get("snippet", {})
    t = sn.get("type")
    if t == "newSponsorEvent":
        return "member", 1, None, None
    if t == "membershipGiftingEvent":
        n = (sn.get("membershipGiftingDetails") or {}).get("giftMembershipsCount", 1)
        return "gift", int(n or 1), None, None
    if t == "superChatEvent":
        d = sn.get("superChatDetails") or {}
        micros = d.get("amountMicros")
        if micros is None:
            return None
        return "superchat", 0, int(micros) / 1_000_000, d.get("currency")
    return None


MAX_RESULTS = 200
UNITS_PER_CALL = 5  # biaya liveChatMessages.list
DAILY_QUOTA = 10_000
# Alasan 403 yang tidak akan sembuh dengan mencoba lagi.
FATAL_REASONS = {
    "quotaExceeded": "kuota harian YouTube API habis (reset tengah malam waktu Pasifik)",
    "dailyLimitExceeded": "kuota harian YouTube API habis",
    "liveChatEnded": "live chat sudah berakhir",
    "liveChatDisabled": "live chat dimatikan",
    "forbidden": "akses ditolak (pastikan login dengan akun pemilik channel)",
    "insufficientPermissions": "izin akun Google kurang",
}


def error_reason(r: httpx.Response) -> str:
    try:
        errs = r.json().get("error", {}).get("errors") or [{}]
        return errs[0].get("reason", "")
    except ValueError:
        return ""


class ApiSource(Source):
    name = "api"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._day = time.strftime("%Y-%m-%d")
        self.calls_today = 0

    def _count_call(self):
        day = time.strftime("%Y-%m-%d")
        if day != self._day:
            self._day, self.calls_today = day, 0
        self.calls_today += 1

    def _quota_info(self):
        used = self.calls_today * UNITS_PER_CALL
        return f"terhubung ke chat · ±{used:,} / {DAILY_QUOTA:,} unit kuota terpakai sesi ini".replace(",", ".")

    def run(self):
        creds = load_credentials()
        vid = video_id_from(self.get_settings().get("video_url", ""))
        if not vid:
            raise FatalSourceError("URL live belum diisi")
        with httpx.Client(timeout=20) as http:
            headers = {"Authorization": f"Bearer {creds.token}"}
            r = http.get(f"{API}/videos", params={"part": "liveStreamingDetails", "id": vid}, headers=headers)
            self._check(r)
            items = r.json().get("items", [])
            chat_id = items and items[0].get("liveStreamingDetails", {}).get("activeLiveChatId")
            if not chat_id:
                raise FatalSourceError(
                    "Live chat aktif tidak ditemukan (live belum mulai / chat dimatikan / URL salah)"
                )
            self.connected()
            self.info = self._quota_info()
            page = None
            while not self._stop.is_set():
                if not creds.valid:
                    creds = load_credentials()
                    headers = {"Authorization": f"Bearer {creds.token}"}
                params = {"liveChatId": chat_id, "part": "snippet,authorDetails", "maxResults": MAX_RESULTS}
                if page:
                    params["pageToken"] = page
                r = http.get(f"{API}/liveChat/messages", params=params, headers=headers)
                self._count_call()
                if r.status_code == 401:
                    creds = load_credentials(force_refresh=True)
                    headers = {"Authorization": f"Bearer {creds.token}"}
                    continue
                self._check(r)
                data = r.json()
                page = data.get("nextPageToken")
                items = data.get("items", [])
                for item in items:
                    parsed = parse_item(item)
                    if not parsed:
                        continue
                    kind, units, amount, currency = parsed
                    author = (item.get("authorDetails") or {}).get("displayName", "")
                    self.emit(item.get("id"), kind, author, units, amount, currency)
                self.info = self._quota_info()
                if len(items) >= MAX_RESULTS and page:
                    continue  # masih ada antrean pesan: ambil halaman berikutnya segera
                wait = max(
                    float(self.get_settings().get("api_poll_seconds", 45)),
                    data.get("pollingIntervalMillis", 5000) / 1000,
                )
                if self.sleep(wait):
                    break
            self.info = "berhenti"

    @staticmethod
    def _check(r: httpx.Response):
        if r.status_code == 403:
            reason = error_reason(r)
            raise FatalSourceError(FATAL_REASONS.get(reason, f"API menolak ({reason or r.text[:120]})"))
        if r.status_code == 404:
            raise FatalSourceError("Video / live chat tidak ditemukan")
        r.raise_for_status()  # 429/5xx -> error biasa, disambung ulang otomatis
