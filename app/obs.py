"""Klien kecil OBS WebSocket (protokol obs-websocket v5, bawaan OBS 28+) untuk ganti scene saat goal tercapai.

Hanya tiga request yang dipakai: GetVersion, GetSceneList, SetCurrentProgramScene.
Protokol: https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md
"""

import base64
import hashlib
import json
import uuid
from contextlib import contextmanager

from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI
from websockets.sync.client import ClientConnection, connect

TIMEOUT = 3
OP_HELLO, OP_IDENTIFY, OP_IDENTIFIED, OP_REQUEST, OP_RESPONSE = 0, 1, 2, 6, 7
CLOSE_AUTH_FAILED = 4009
CONNECT_INFO = "Tools > WebSocket Server Settings > Show Connect Info"


class ObsError(Exception):
    """Pesan siap tampil untuk user."""


class RequestFailed(Exception):
    def __init__(self, code: int, comment: str):
        super().__init__(f"{code}: {comment}")
        self.code = code


def auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest())
    return base64.b64encode(hashlib.sha256(secret + challenge.encode()).digest()).decode()


class ObsClient:
    def __init__(self, ws: ClientConnection):
        self.ws = ws

    def identify(self, password: str):
        hello = self._recv()
        identify = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = hello["d"].get("authentication")
        if auth:
            if not password:
                raise ObsError(f"OBS meminta password. Salin password dari OBS ({CONNECT_INFO}).")
            identify["authentication"] = auth_string(password, auth["salt"], auth["challenge"])
        self._send(OP_IDENTIFY, identify)
        try:
            msg = self._recv()
        except ConnectionClosed as e:
            if e.rcvd and e.rcvd.code == CLOSE_AUTH_FAILED:
                raise ObsError(f"Password OBS salah. Salin ulang dari OBS ({CONNECT_INFO}).") from None
            raise ObsError(f"OBS menutup koneksi ({e.rcvd.reason if e.rcvd else 'tanpa alasan'}).") from None
        if msg.get("op") != OP_IDENTIFIED:
            raise ObsError("Respons OBS tidak dikenali. Pastikan memakai OBS 28 atau lebih baru.")

    def request(self, request_type: str, data: dict | None = None) -> dict:
        request_id = uuid.uuid4().hex
        payload = {"requestType": request_type, "requestId": request_id}
        if data:
            payload["requestData"] = data
        self._send(OP_REQUEST, payload)
        while True:
            msg = self._recv()
            if msg.get("op") == OP_RESPONSE and msg["d"].get("requestId") == request_id:
                break
        status = msg["d"]["requestStatus"]
        if not status["result"]:
            raise RequestFailed(status.get("code", 0), status.get("comment", ""))
        return msg["d"].get("responseData") or {}

    def _send(self, op: int, data: dict):
        self.ws.send(json.dumps({"op": op, "d": data}))

    def _recv(self) -> dict:
        try:
            return json.loads(self.ws.recv(timeout=TIMEOUT))
        except TimeoutError:
            raise ObsError("OBS tidak merespons (timeout).") from None


@contextmanager
def session(host: str, port: int, password: str):
    """Koneksi yang sudah terautentikasi ke OBS; ditutup otomatis."""
    # Di Windows "localhost" dicoba lewat IPv6 dulu (~2 detik terbuang); OBS mendengarkan di IPv4.
    if host.strip().lower() == "localhost":
        host = "127.0.0.1"
    try:
        conn = connect(f"ws://{host}:{int(port)}", open_timeout=TIMEOUT, close_timeout=1)
    except ConnectionRefusedError:
        raise ObsError(
            "OBS WebSocket belum aktif. Di OBS buka Tools > WebSocket Server Settings, "
            "centang Enable WebSocket server, lalu klik Apply."
        ) from None
    except (TimeoutError, OSError, InvalidHandshake, InvalidURI) as e:
        raise ObsError(
            f"OBS tidak menjawab di {host}:{port}. Pastikan OBS terbuka dan host/port benar ({type(e).__name__})."
        ) from None
    with conn as ws:
        client = ObsClient(ws)
        client.identify(password)
        yield client


def scene_info(host: str, port: int, password: str) -> dict:
    """Versi OBS, daftar scene (urutan seperti panel Scenes di OBS), dan scene yang sedang tayang."""
    with session(host, port, password) as obs:
        version = obs.request("GetVersion")["obsVersion"]
        resp = obs.request("GetSceneList")
    # OBS mengirim scene dari bawah ke atas; dibalik agar sama dengan panel Scenes.
    scenes = [s["sceneName"] for s in reversed(resp["scenes"])]
    return {"version": version, "scenes": scenes, "current": resp.get("currentProgramSceneName")}


def switch_scene(host: str, port: int, password: str, scene: str):
    with session(host, port, password) as obs:
        try:
            obs.request("SetCurrentProgramScene", {"sceneName": scene})
        except RequestFailed:
            raise ObsError(
                f"Scene '{scene}' tidak ada di OBS. Nama harus sama persis, termasuk huruf besar-kecil."
            ) from None
