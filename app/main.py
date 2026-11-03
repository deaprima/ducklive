"""Server lokal: halaman Admin, overlay OBS, REST API, dan WebSocket untuk update realtime."""

import asyncio
import csv
import io
import json
import os
import secrets
import socket
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, obs
from .db import DB
from .engine import OVERLAYS, Engine
from .paths import data_dir, db_path, resource_dir
from .sources.api import ApiSource, client_secret_path, login_interactive, token_path
from .sources.scrape import ScrapeSource

WEB = resource_dir() / "web"
APP_ID = "ducklive"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MIN_TOKEN_LEN = 16


def host_only(netloc: str) -> str:
    """'127.0.0.1:8765' -> '127.0.0.1', '[::1]:8765' -> '[::1]'."""
    netloc = (netloc or "").strip().lower()
    if netloc.startswith("["):
        return netloc.split("]")[0] + "]"
    return netloc.split(":")[0]


class Hub:
    """Kumpulan koneksi WebSocket. push() aman dipanggil dari thread lain."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    @property
    def active(self):
        return bool(self.loop and self.clients)

    def push(self, message: dict):
        if self.active:
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._broadcast(message)))

    async def _send(self, ws: WebSocket, text: str):
        try:
            # Satu client yang macet tidak boleh menahan overlay lain.
            await asyncio.wait_for(ws.send_text(text), timeout=3)
        except Exception:
            self.clients.discard(ws)

    async def _broadcast(self, message: dict):
        text = json.dumps(message)
        await asyncio.gather(*(self._send(ws, text) for ws in list(self.clients)))


def load_admin_token() -> str:
    env = os.environ.get("DUCKLIVE_TOKEN")
    if env:
        return env
    p = data_dir() / "admin_token.txt"
    if p.exists():
        stored = p.read_text().strip()
        # File kosong/rusak (mis. tulis pertama terputus) tidak boleh jadi token: buat ulang.
        if len(stored) >= MIN_TOKEN_LEN:
            return stored
    token = secrets.token_urlsafe(24)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(token)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)  # atomik: file token tidak pernah terlihat setengah tertulis
    return token


def create_app(db_file=None, admin_token=None, lan=False) -> FastAPI:
    """lan=False: hanya menerima Host 127.0.0.1/localhost (menangkal DNS rebinding)."""
    hub = Hub()
    db = DB(db_file or db_path())
    token = admin_token or load_admin_token()
    state = {"source": None}

    def toast(text):
        hub.push({"type": "toast", "text": text})

    def switch_scene_for_goal(goal):
        s = engine.settings
        if not (s["obs_enabled"] and goal.get("scene")):
            return

        def work():
            try:
                obs.switch_scene(s["obs_host"], s["obs_port"], s["obs_password"], goal["scene"])
                toast(f"OBS pindah ke scene '{goal['scene']}' (goal: {goal['text']})")
            except Exception as e:
                toast(f"Gagal pindah scene OBS: {e}")

        threading.Thread(target=work, daemon=True).start()

    def push_state():
        if hub.active:  # snapshot cukup mahal; hanya dihitung jika ada yang mendengarkan
            hub.push({"type": "state", "state": engine.snapshot()})

    engine = Engine(db, on_change=push_state, on_alert=hub.push, on_goal=switch_scene_for_goal)

    def emit(ext_id, kind, author, units=1, amount=None, currency=None):
        engine.handle_event(ext_id, kind, author, units, amount, currency)

    def make_source():
        cls = {"scrape": ScrapeSource, "api": ApiSource}.get(engine.settings["source"])
        return cls(emit, lambda: engine.settings, notify=toast) if cls else None

    @asynccontextmanager
    async def lifespan(app):
        hub.loop = asyncio.get_running_loop()

        async def ticker():
            n = 0
            while True:
                await asyncio.sleep(1)
                n += 1
                engine.tick()
                if n % 5 == 0:
                    engine.persist()
                    push_state()

        task = asyncio.create_task(ticker())
        yield
        task.cancel()
        if state["source"]:
            state["source"].stop()
        engine.persist()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.shutdown = None

    def host_allowed(host_header: str) -> bool:
        return lan or host_only(host_header) in LOCAL_HOSTS

    @app.middleware("http")
    async def check_host(request: Request, call_next):
        if not host_allowed(request.headers.get("host", "")):
            return PlainTextResponse("Host tidak diizinkan", status_code=400)
        response = await call_next(request)
        # Anti-clickjacking: situs lain tidak boleh membingkai admin. Overlay tetap boleh
        # dibingkai oleh DuckLive sendiri (preview di admin); OBS memuatnya sebagai halaman utama.
        if request.url.path.startswith("/overlay/"):
            response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        else:
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/static/"):
            # Tanpa ini browser OBS bisa terus memakai CSS/JS lama setelah aplikasi diperbarui.
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(ValueError)
    async def bad_value(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def auth(x_admin_token: str = Header(default="")):
        if not token or not x_admin_token or not secrets.compare_digest(x_admin_token.encode(), token.encode()):
            raise HTTPException(401, "token admin salah")

    admin_only = [Depends(auth)]

    # ---------------------------------------------------------------- halaman
    @app.get("/")
    def root():
        return RedirectResponse("/admin")

    @app.get("/admin")
    def admin():
        return FileResponse(WEB / "admin.html", headers={"Cache-Control": "no-store"})

    @app.get("/overlay/{name}")
    def overlay(name: str):
        if name not in OVERLAYS:
            raise HTTPException(404)
        return FileResponse(WEB / f"overlay_{name}.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

    # ------------------------------------------------------- publik (read-only)
    @app.get("/api/ping")
    def ping():
        return {"app": APP_ID, "version": __version__}

    @app.get("/api/state")
    def get_state():
        return engine.snapshot()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        host = websocket.headers.get("host", "")
        origin = websocket.headers.get("origin")
        # WebSocket tidak dilindungi CORS: tolak halaman dari situs lain.
        if not host_allowed(host) or (origin and host_only(urlsplit(origin).netloc) != host_only(host)):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        hub.clients.add(websocket)
        try:
            await websocket.send_text(json.dumps({"type": "state", "state": engine.snapshot()}))
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(websocket)

    # ------------------------------------------------------------ timer
    @app.post("/api/control/{action}", dependencies=admin_only)
    def control(action: str):
        actions = {"start": engine.start, "pause": engine.pause, "resume": engine.resume, "reset": engine.reset}
        if action not in actions:
            raise HTTPException(404)
        actions[action]()
        return {"ok": True}

    class Minutes(BaseModel):
        minutes: float

    @app.post("/api/time", dependencies=admin_only)
    def adjust(body: Minutes):
        return {"added_seconds": engine.adjust_seconds(body.minutes * 60)}

    class Multiplier(BaseModel):
        value: float
        minutes: float

    @app.post("/api/multiplier", dependencies=admin_only)
    def multiplier(body: Multiplier):
        engine.set_multiplier(body.value, body.minutes)
        return {"ok": True}

    class Counter(BaseModel):
        value: int

    @app.post("/api/counter", dependencies=admin_only)
    def counter(body: Counter):
        engine.set_counter(body.value)
        return {"ok": True}

    # --------------------------------------------------------- pengaturan
    @app.get("/api/settings", dependencies=admin_only)
    def get_settings():
        s = dict(engine.settings)
        s["obs_password"] = "********" if s["obs_password"] else ""
        return s

    @app.post("/api/settings", dependencies=admin_only)
    def update_settings(patch: dict):
        engine.update_settings(patch)
        return {"ok": True}

    # -------------------------------------------------------------- goal
    class GoalIn(BaseModel):
        target: int
        text: str
        bonus_minutes: float = 0
        scene: str = ""

    @app.post("/api/goals", dependencies=admin_only)
    def add_goal(body: GoalIn):
        return {"id": engine.add_goal(body.target, body.text, body.bonus_minutes, body.scene)}

    @app.put("/api/goals/{goal_id}", dependencies=admin_only)
    def update_goal(goal_id: int, patch: dict):
        engine.update_goal(goal_id, patch)
        return {"ok": True}

    @app.delete("/api/goals/{goal_id}", dependencies=admin_only)
    def delete_goal(goal_id: int):
        engine.delete_goal(goal_id)
        return {"ok": True}

    # ------------------------------------------------------------- event
    class TestEvent(BaseModel):
        kind: str = "member"
        author: str = "Tester"
        units: int = 1
        amount: float | None = None
        currency: str | None = None

    @app.post("/api/events/test", dependencies=admin_only)
    def test_event(body: TestEvent):
        ext_id = f"test-{secrets.token_hex(6)}"
        ev = engine.handle_event(ext_id, body.kind, body.author, body.units, body.amount, body.currency, note="tes")
        if ev is None:
            raise HTTPException(400, "event tidak valid")
        return ev

    @app.post("/api/events/{event_id}/undo", dependencies=admin_only)
    def undo(event_id: int):
        if not engine.undo(event_id):
            raise HTTPException(404, "event tidak ditemukan / sudah dibatalkan")
        return {"ok": True}

    @app.get("/api/export.csv", dependencies=admin_only)
    def export_csv():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "waktu_unix", "jenis", "nama", "jumlah", "detik_ditambah", "catatan", "dibatalkan"])
        for r in db.query("SELECT * FROM events ORDER BY id"):
            name = r["author"]
            if name[:1] in ("=", "+", "-", "@"):  # cegah CSV injection saat dibuka di Excel
                name = "'" + name
            w.writerow([r["id"], r["ts"], r["kind"], name, r["units"], r["seconds_added"], r["note"], r["undone"]])
        headers = {"Content-Disposition": "attachment; filename=events.csv"}
        return Response(buf.getvalue(), media_type="text/csv", headers=headers)

    # ---------------------------------------------------------- sumber live
    @app.post("/api/source/{action}", dependencies=admin_only)
    def source(action: str):
        if action not in ("start", "stop"):
            raise HTTPException(404)
        if state["source"]:
            state["source"].stop()
        if action == "start":
            src = make_source()
            if not src:
                raise HTTPException(400, "sumber 'manual' tidak perlu dijalankan")
            state["source"] = src
            src.start()
        return {"ok": True}

    @app.get("/api/source/status", dependencies=admin_only)
    def source_status():
        src = state["source"]
        return {
            **(src.status() if src else {"name": "none", "running": False, "error": "", "info": ""}),
            "google_client_secret": client_secret_path().exists(),
            "google_logged_in": token_path().exists(),
            "data_dir": str(data_dir()),
        }

    @app.post("/api/google/login", dependencies=admin_only)
    def google_login():
        def work():
            try:
                login_interactive()
                toast("Login Google berhasil")
            except Exception as e:
                toast(f"Login Google gagal: {e}")

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    # --------------------------------------------------------------- OBS
    def obs_call(fn, *args):
        s = engine.settings
        try:
            return fn(s["obs_host"], s["obs_port"], s["obs_password"], *args)
        except obs.ObsError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/obs/scenes", dependencies=admin_only)
    def obs_scenes():
        return obs_call(obs.scene_info)

    class SceneIn(BaseModel):
        scene: str

    @app.post("/api/obs/scene", dependencies=admin_only)
    def obs_switch(body: SceneIn):
        obs_call(obs.switch_scene, body.scene)
        return {"ok": True}

    # ----------------------------------------------------------- aplikasi
    @app.post("/api/open-data-dir", dependencies=admin_only)
    def open_data_dir():
        if os.name != "nt":
            raise HTTPException(400, "hanya tersedia di Windows")
        os.startfile(data_dir())
        return {"ok": True}

    @app.post("/api/shutdown", dependencies=admin_only)
    def shutdown():
        if not app.state.shutdown:
            raise HTTPException(400, "tidak didukung")
        engine.persist()
        threading.Timer(0.5, app.state.shutdown).start()
        return {"ok": True}

    app.state.engine = engine
    app.state.token = token
    return app


# ------------------------------------------------------------------- run
def message_box(text: str, error=False):
    """Pesan untuk versi .exe (tanpa jendela konsol). Di luar Windows cukup print."""
    print(text)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, "DuckLive", 0x10 if error else 0x40)
        except Exception:
            pass


def redirect_output_if_windowed():
    """Pada .exe tanpa konsol, sys.stdout/stderr bernilai None: arahkan ke log.txt di folder data."""
    if sys.stdout is None or sys.stderr is None:
        log = open(data_dir() / "log.txt", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def existing_instance(port: int):
    """'self' jika DuckLive sudah berjalan di port ini, 'other' jika port dipakai aplikasi lain."""
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/ping", timeout=1.5)
        if r.status_code == 200 and r.json().get("app") == APP_ID:
            return "self"
    except Exception:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return "other"
    return None


def run():
    import uvicorn

    redirect_output_if_windowed()
    lan = os.environ.get("DUCKLIVE_LAN") == "1"
    host = "0.0.0.0" if lan else "127.0.0.1"
    port = int(os.environ.get("DUCKLIVE_PORT", "8765"))
    open_browser = os.environ.get("DUCKLIVE_NO_BROWSER") != "1"

    found = existing_instance(port)
    if found == "self":  # ikon diklik lagi saat aplikasi sudah berjalan: cukup buka Admin
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{port}/admin#token={load_admin_token()}")
        return
    if found == "other":
        message_box(
            f"Port {port} sedang dipakai aplikasi lain, jadi DuckLive tidak bisa berjalan.\n\n"
            "Tutup aplikasi tersebut lalu coba lagi, atau restart komputer.",
            error=True,
        )
        return

    app = create_app(lan=lan)
    url = f"http://127.0.0.1:{port}/admin#token={app.state.token}"
    print("=" * 60)
    print(f" DuckLive {__version__} berjalan di http://127.0.0.1:{port}")
    print(f" Admin : {url}")
    for name in OVERLAYS:
        print(f" OBS   : http://127.0.0.1:{port}/overlay/{name}")
    if lan:
        print(" MODE LAN AKTIF: admin dapat diakses dari perangkat lain di jaringan ini (butuh token).")
    print("=" * 60)

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    app.state.shutdown = lambda: setattr(server, "should_exit", True)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.run()
    except Exception as e:
        message_box(f"DuckLive berhenti karena error:\n{e}\n\nDetail ada di {data_dir() / 'log.txt'}", error=True)
        raise
