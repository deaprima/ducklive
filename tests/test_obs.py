import json
import socket
import threading

import pytest
from fastapi.testclient import TestClient
from websockets.sync.server import serve

from app import obs
from app.main import create_app
from app.obs import ObsError, auth_string

PASSWORD = "rahasia"
SALT, CHALLENGE = "c2FsdA==", "Y2hhbGxlbmdl"


class FakeObs:
    """Server obs-websocket v5 tiruan: auth password, GetVersion, GetSceneList, SetCurrentProgramScene."""

    scenes = ["Utama", "Kamera Dapur", "Istirahat"]  # urutan panel Scenes di OBS (atas ke bawah)

    def __init__(self, password=PASSWORD):
        self.password = password
        self.current = "Utama"
        self.server = serve(self.handle, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def handle(self, ws):
        hello = {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}
        if self.password:
            hello["authentication"] = {"salt": SALT, "challenge": CHALLENGE}
        ws.send(json.dumps({"op": 0, "d": hello}))
        ident = json.loads(ws.recv())["d"]
        if self.password and ident.get("authentication") != auth_string(self.password, SALT, CHALLENGE):
            ws.close(4009, "Authentication failed.")
            return
        ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        for raw in ws:
            req = json.loads(raw)["d"]
            ok, resp = True, {}
            if req["requestType"] == "GetVersion":
                resp = {"obsVersion": "33.0.1"}
            elif req["requestType"] == "GetSceneList":
                # OBS mengirim dari bawah ke atas
                scenes = [{"sceneName": n} for n in reversed(self.scenes)]
                resp = {"currentProgramSceneName": self.current, "scenes": scenes}
            elif req["requestType"] == "SetCurrentProgramScene":
                ok = req["requestData"]["sceneName"] in self.scenes
                if ok:
                    self.current = req["requestData"]["sceneName"]
            status = {"result": ok, "code": 100 if ok else 600}
            ws.send(json.dumps({"op": 7, "d": {**req, "requestStatus": status, "responseData": resp}}))

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake_obs():
    server = FakeObs()
    yield server
    server.close()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_scene_info_and_switch(fake_obs):
    info = obs.scene_info("localhost", fake_obs.port, PASSWORD)
    assert info == {"version": "33.0.1", "scenes": ["Utama", "Kamera Dapur", "Istirahat"], "current": "Utama"}
    obs.switch_scene("localhost", fake_obs.port, PASSWORD, "Kamera Dapur")
    assert fake_obs.current == "Kamera Dapur"


def test_unknown_scene(fake_obs):
    with pytest.raises(ObsError, match="tidak ada di OBS"):
        obs.switch_scene("127.0.0.1", fake_obs.port, PASSWORD, "kamera dapur")  # huruf kecil: beda nama


def test_password_errors(fake_obs):
    with pytest.raises(ObsError, match="Password OBS salah"):
        obs.scene_info("127.0.0.1", fake_obs.port, "salah")
    with pytest.raises(ObsError, match="meminta password"):
        obs.scene_info("127.0.0.1", fake_obs.port, "")


def test_no_password_required():
    server = FakeObs(password="")
    try:
        assert obs.scene_info("127.0.0.1", server.port, "")["version"] == "33.0.1"
    finally:
        server.close()


def test_websocket_not_enabled():
    with pytest.raises(ObsError, match="belum aktif"):
        obs.scene_info("127.0.0.1", free_port(), "")


def test_api_reports_obs_errors_as_400(tmp_path):
    app = create_app(db_file=tmp_path / "a.db", admin_token="t")
    h = {"X-Admin-Token": "t"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.post("/api/settings", headers=h, json={"obs_port": free_port()})
        r = c.get("/api/obs/scenes", headers=h)
        assert r.status_code == 400 and "belum aktif" in r.json()["detail"]
        assert c.post("/api/obs/scene", headers=h, json={"scene": "X"}).status_code == 400
        assert c.get("/api/obs/scenes").status_code == 401


def test_api_switches_scene(tmp_path, fake_obs):
    app = create_app(db_file=tmp_path / "a.db", admin_token="t")
    h = {"X-Admin-Token": "t"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.post("/api/settings", headers=h, json={"obs_port": fake_obs.port, "obs_password": PASSWORD})
        assert c.get("/api/obs/scenes", headers=h).json()["scenes"] == FakeObs.scenes
        assert c.post("/api/obs/scene", headers=h, json={"scene": "Istirahat"}).status_code == 200
    assert fake_obs.current == "Istirahat"
