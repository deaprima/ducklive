import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import __version__, main
from app.main import create_app

TOKEN = "t0ken"
WS_HOST = {"host": "127.0.0.1:8765"}  # TestClient mengabaikan base_url untuk WebSocket


def make(tmp_path):
    app = create_app(db_file=tmp_path / "a.db", admin_token=TOKEN)
    return TestClient(app, base_url="http://127.0.0.1:8765"), {"X-Admin-Token": TOKEN}


def test_auth_required(tmp_path):
    c, h = make(tmp_path)
    with c:
        assert c.post("/api/control/start").status_code == 401
        assert c.post("/api/control/start", headers={"X-Admin-Token": "salah"}).status_code == 401
        assert c.get("/api/settings").status_code == 401
        assert c.get("/api/state").status_code == 200  # read-only publik untuk overlay
        assert c.post("/api/control/start", headers=h).status_code == 200


def test_state_hides_secrets(tmp_path):
    c, h = make(tmp_path)
    with c:
        c.post("/api/settings", headers=h, json={"obs_password": "rahasia"})
        assert "rahasia" not in c.get("/api/state").text
        assert c.get("/api/settings", headers=h).json()["obs_password"] == "********"


def test_flow_goal_undo_ws(tmp_path):
    c, h = make(tmp_path)
    with c:
        c.post("/api/goals", headers=h, json={"target": 5, "text": "Goal A", "bonus_minutes": 1})
        c.post("/api/control/start", headers=h)
        with c.websocket_connect("/ws", headers=WS_HOST) as ws:
            assert ws.receive_json()["type"] == "state"
            ev = c.post("/api/events/test", headers=h, json={"kind": "gift", "author": "Sari", "units": 5}).json()
            kinds = []
            for _ in range(6):
                kinds.append(ws.receive_json()["type"])
                if "goal" in kinds:
                    break
            assert "event" in kinds and "goal" in kinds
        st = c.get("/api/state").json()
        assert st["counter"] == 5 and st["goals"][0]["done"]
        assert c.post(f"/api/events/{ev['id']}/undo", headers=h).status_code == 200
        assert c.get("/api/state").json()["counter"] == 0
        assert c.post(f"/api/events/{ev['id']}/undo", headers=h).status_code == 404


def test_csv_injection_and_xss_payload_stored_as_text(tmp_path):
    c, h = make(tmp_path)
    with c:
        c.post("/api/events/test", headers=h, json={"kind": "member", "author": '=HYPERLINK("x")'})
        csv = c.get("/api/export.csv", headers=h).text
        assert "'=HYPERLINK" in csv


def test_pages_served(tmp_path):
    c, h = make(tmp_path)
    with c:
        for p in (
            "/admin",
            "/overlay/timer",
            "/overlay/milestone",
            "/overlay/goals",
            "/overlay/alerts",
            "/overlay/ticker",
            "/static/common.js",
            "/static/fonts/Poppins-ExtraBold.ttf",
            "/static/brand/logo-mark.svg",
            "/static/brand/logo-mark-dark.svg",
            *(f"/static/brand/mascot-{f}.svg" for f in ("normal", "happy", "sleepy", "celebrate")),
        ):
            assert c.get(p).status_code == 200, p
        assert c.get("/overlay/xyz").status_code == 404


def test_rejects_foreign_host_and_origin(tmp_path):
    c, h = make(tmp_path)
    with c:
        # DNS rebinding: Host bukan localhost ditolak
        assert c.get("/api/state", headers={"host": "evil.example"}).status_code == 400
        # situs lain tidak boleh membaca WebSocket
        evil = {**WS_HOST, "origin": "https://evil.example"}
        with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws", headers=evil) as ws:
            ws.receive_json()
        with c.websocket_connect("/ws", headers={**WS_HOST, "origin": "http://127.0.0.1:8765"}) as ws:
            assert ws.receive_json()["type"] == "state"


def test_lan_mode_allows_other_hosts(tmp_path):
    app = create_app(db_file=tmp_path / "a.db", admin_token=TOKEN, lan=True)
    with TestClient(app, base_url="http://192.168.1.5:8765") as c:
        assert c.get("/api/state").status_code == 200


def test_invalid_numbers_return_400_without_partial_update(tmp_path):
    c, h = make(tmp_path)
    with c:
        r = c.post("/api/settings", headers=h, json={"goals_title": "BARU", "minutes_per_member": ""})
        assert r.status_code == 400 and "Menit per member" in r.json()["detail"]
        assert c.get("/api/state").json()["settings"]["goals_title"] == "IMPACTHON"
        assert c.post("/api/settings", headers=h, json={"max_hours": "inf"}).status_code == 400
        assert c.post("/api/goals", headers=h, json={"target": 5, "text": "x", "bonus_minutes": "nan"}).status_code in (
            400,
            422,
        )
        assert c.get("/api/state").status_code == 200


def test_ping_and_shutdown(tmp_path):
    c, h = make(tmp_path)
    with c:
        assert c.get("/api/ping").json() == {"app": "ducklive", "version": __version__}
        assert c.post("/api/shutdown").status_code == 401
        assert c.post("/api/shutdown", headers=h).status_code == 400  # tanpa server uvicorn


def test_static_files_not_cached_stale(tmp_path):
    # OBS harus selalu mengambil CSS/JS terbaru, kalau tidak setting gaya baru tidak terpakai
    c, h = make(tmp_path)
    with c:
        for p in ("/static/common.js", "/static/common.css"):
            assert c.get(p).headers["cache-control"] == "no-cache", p
        assert c.get("/overlay/timer").headers["cache-control"] == "no-store"


def test_style_settings_reach_overlay_state(tmp_path):
    c, h = make(tmp_path)
    with c:
        assert (
            c.post(
                "/api/settings",
                headers=h,
                json={"overlay_scale": 130, "overlay_styles": {"goals": {"theme": "transparent"}}},
            ).status_code
            == 200
        )
        s = c.get("/api/state").json()["settings"]
        assert s["overlay_scale"] == 130 and s["overlay_styles"] == {"goals": {"theme": "transparent"}}


@pytest.mark.parametrize("content", ["", "   \n", "pendek"])
def test_empty_or_short_token_file_is_regenerated(tmp_path, monkeypatch, content):
    # File token kosong tidak boleh membuat semua route admin terbuka tanpa token
    monkeypatch.delenv("DUCKLIVE_TOKEN", raising=False)
    monkeypatch.setattr(main, "data_dir", lambda: tmp_path)
    (tmp_path / "admin_token.txt").write_text(content)
    token = main.load_admin_token()
    assert len(token) >= main.MIN_TOKEN_LEN
    assert (tmp_path / "admin_token.txt").read_text() == token
    c = TestClient(create_app(db_file=tmp_path / "a.db"), base_url="http://127.0.0.1:8765")
    with c:
        assert c.post("/api/control/pause").status_code == 401
        assert c.post("/api/control/pause", headers={"X-Admin-Token": ""}).status_code == 401
        assert c.post("/api/control/pause", headers={"X-Admin-Token": token}).status_code == 200


def test_empty_server_token_never_authenticates(tmp_path):
    c = TestClient(create_app(db_file=tmp_path / "a.db", admin_token=""), base_url="http://127.0.0.1:8765")
    with c:
        # admin_token="" jatuh ke load_admin_token(), jadi tetap ada token acak
        assert c.post("/api/control/pause").status_code == 401


def test_admin_cannot_be_framed_but_overlay_preview_can(tmp_path):
    c, h = make(tmp_path)
    with c:
        r = c.get("/admin")
        assert r.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        r = c.get("/overlay/timer")
        assert r.headers["x-frame-options"] == "SAMEORIGIN"
        assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
