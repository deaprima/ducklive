"""Inti aplikasi: timer, hitungan, goal, undo, multiplier, dan pengaturan.

Engine tidak tahu event datang dari mana (YouTube API, baca live chat, atau tombol tes).
"""

import math
import re
import threading
import time

from .db import DB

DEFAULTS = {
    "minutes_per_member": 3.0,
    "minutes_per_gift": 3.0,
    "max_hours": 100.0,
    "initial_minutes": 60.0,
    "superchat_currency": "IDR",
    "superchat_amount_per_step": 10000.0,
    "superchat_minutes_per_step": 1.0,
    "goals_title": "IMPACTHON",
    "unit_label": "Member",
    "rule_text": "",
    "sound": True,
    "mascot": True,
    # Gaya overlay
    "theme": "dark",
    "accent": "#f4a93c",
    "font": "Poppins, system-ui, sans-serif",
    "overlay_scale": 100.0,  # persen
    "card_opacity": 100.0,  # persen
    "corner_radius": 20.0,  # px
    "card_border": True,
    "text_shadow": True,
    "align": "center",
    "overlay_styles": {},  # gaya khusus per overlay: {"goals": {"theme": "transparent"}}
    # Overlay live chat
    "chat_max_items": 8.0,
    "chat_hide_links": True,
    "chat_show_owner": False,  # chat biasa dari pemilik channel (host) sendiri
    "chat_show_avatars": True,
    # Sumber event
    "source": "manual",
    "video_url": "",
    # 45 dtk ≈ 1.920 panggilan/hari ≈ 9.600 unit, di bawah kuota default YouTube API 10.000 unit/hari.
    "api_poll_seconds": 45.0,
    "obs_enabled": False,
    "obs_host": "localhost",
    "obs_port": 4455,
    "obs_password": "",
}
SECRET_KEYS = {"obs_password"}
FLOAT_KEYS = {
    "minutes_per_member",
    "minutes_per_gift",
    "max_hours",
    "initial_minutes",
    "superchat_amount_per_step",
    "superchat_minutes_per_step",
    "api_poll_seconds",
    "overlay_scale",
    "card_opacity",
    "corner_radius",
    "chat_max_items",
}
# Batas atas agar angka salah ketik (mis. 1e308) tidak merusak timer atau JSON.
FLOAT_MAX = {
    "max_hours": 10_000.0,
    "initial_minutes": 600_000.0,
    "api_poll_seconds": 3600.0,
    "overlay_scale": 200.0,
    "card_opacity": 100.0,
    "corner_radius": 40.0,
    "chat_max_items": 15.0,
}
FLOAT_MIN = {"api_poll_seconds": 5.0, "overlay_scale": 50.0, "chat_max_items": 1.0}
INT_KEYS = {"obs_port"}
BOOL_KEYS = {
    "sound",
    "obs_enabled",
    "mascot",
    "card_border",
    "text_shadow",
    "chat_hide_links",
    "chat_show_owner",
    "chat_show_avatars",
}
STR_KEYS = {
    "goals_title",
    "unit_label",
    "rule_text",
    "superchat_currency",
    "video_url",
    "obs_host",
    "obs_password",
    "font",
    "accent",
    "theme",
    "source",
    "align",
}
# Default versi lama; diganti saat dimuat agar user lama ikut tampilan baru.
OLD_DEFAULTS = {"accent": "#e53935", "font": "Poppins, Inter, system-ui, sans-serif"}
LABELS = {
    "minutes_per_member": "Menit per member",
    "minutes_per_gift": "Menit per gift",
    "max_hours": "Batas maksimum",
    "initial_minutes": "Waktu awal",
    "superchat_amount_per_step": "Super Chat: tiap berapa",
    "superchat_minutes_per_step": "Super Chat: menit",
    "api_poll_seconds": "Interval API",
    "obs_port": "OBS port",
    "chat_max_items": "Jumlah pesan chat",
}
CHOICES = {
    "theme": {"dark", "light", "neon", "transparent"},
    "source": {"manual", "scrape", "api"},
    "align": {"left", "center", "right"},
}
OVERLAYS = ("timer", "milestone", "goals", "alerts", "ticker", "chat")
# Pengaturan yang boleh berbeda per overlay (lihat overlay_styles).
STYLE_KEYS = {
    "theme",
    "accent",
    "card_opacity",
    "corner_radius",
    "card_border",
    "font",
    "overlay_scale",
    "align",
    "text_shadow",
}
COUNTED_KINDS = {"member", "gift"}

EVENT_INSERT = (
    "INSERT INTO events(ts,ext_id,kind,author,units,counter_delta,seconds_added,note) VALUES(?,?,?,?,?,?,?,?)"
)


def finite(value, what="angka") -> float:
    """float() yang menolak teks kosong, NaN, dan tak hingga dengan pesan yang bisa dibaca user."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: '{value}' bukan angka") from None
    if not math.isfinite(v):
        raise ValueError(f"{what}: angka tidak valid")
    return v


def fmt_num(x: float) -> str:
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:g}"


class Engine:
    def __init__(self, db: DB, clock=time.time, on_change=None, on_alert=None, on_goal=None):
        self.db = db
        self.clock = clock
        self.lock = threading.RLock()
        self.on_change = on_change or (lambda: None)
        self.on_alert = on_alert or (lambda payload: None)
        self.on_goal = on_goal or (lambda goal: None)
        self._load()

    # ------------------------------------------------------------------ state
    def _load(self):
        stored = self.db.kv_get("settings", {})
        self.settings = dict(DEFAULTS)
        self.settings.update({k: v for k, v in stored.items() if k in DEFAULTS and OLD_DEFAULTS.get(k) != v})
        timer = self.db.kv_get("timer", None)
        if timer:
            self.status = timer["status"]
            self._rem = float(timer["remaining"])
            # Setelah aplikasi dibuka ulang, timer dilanjutkan manual agar waktu tidak berkurang diam-diam.
            if self.status == "running":
                self.status = "paused"
        else:
            self.status = "idle"
            self._rem = self.settings["initial_minutes"] * 60
        self._ref = self.clock()
        self.counter = int(self.db.kv_get("counter", 0))
        self.multiplier = self.db.kv_get("multiplier", {"value": 1.0, "until": 0})

    def persist(self):
        with self.lock:
            now = self.clock()
            self.db.kv_set("timer", {"status": self.status, "remaining": self._remaining(now)})
            self.db.kv_set("counter", self.counter)
            self.db.kv_set("multiplier", self.multiplier)
            self.db.kv_set("settings", self.settings)

    def _remaining(self, now):
        if self.status == "running":
            return max(0.0, self._rem - (now - self._ref))
        return self._rem

    def remaining(self):
        with self.lock:
            return self._remaining(self.clock())

    def _cap(self):
        return max(0.0, self.settings["max_hours"]) * 3600

    def _set_rem(self, value, now=None):
        self._rem = max(0.0, value)
        self._ref = now if now is not None else self.clock()

    def _changed(self):
        self.persist()
        self.on_change()

    # ---------------------------------------------------------------- control
    def tick(self):
        """Dipanggil tiap detik. Menandai timer selesai saat habis."""
        with self.lock:
            if self.status == "running" and self._remaining(self.clock()) <= 0:
                self.status = "ended"
                self._set_rem(0)
                self._changed()
                return True
        return False

    def start(self):
        with self.lock:
            if self.status not in ("idle", "paused"):
                return
            self.status = "running"
            self._ref = self.clock()
            self._changed()

    resume = start

    def pause(self):
        with self.lock:
            if self.status == "running":
                self._set_rem(self._remaining(self.clock()))
                self.status = "paused"
                self._changed()

    def reset(self):
        with self.lock:
            self.status = "idle"
            self._set_rem(self.settings["initial_minutes"] * 60)
            self.counter = 0
            self.multiplier = {"value": 1.0, "until": 0}
            self.db.execute("UPDATE goals SET reached_at=NULL, bonus_applied=0, manual_done=NULL")
            self.db.execute("DELETE FROM events")
            self._changed()

    def adjust_seconds(self, delta):
        """Tambah/kurangi waktu manual (tidak mengubah hitungan goal)."""
        delta = finite(delta, "Menit")
        with self.lock:
            now = self.clock()
            cur = self._remaining(now)
            new = min(self._cap(), max(0.0, cur + delta))
            self._set_rem(new, now)
            if self.status == "ended" and new > 0:
                self.status = "paused"
            self.db.execute(EVENT_INSERT, (now, None, "manual", "admin", 0, 0, new - cur, "penyesuaian manual"))
            self._changed()
            return new - cur

    def set_multiplier(self, value, minutes):
        value = max(1.0, min(finite(value, "Multiplier"), 100.0))
        minutes = min(finite(minutes, "Durasi multiplier"), 7 * 24 * 60)
        with self.lock:
            if value == 1.0 or minutes <= 0:
                self.multiplier = {"value": 1.0, "until": 0}
            else:
                self.multiplier = {"value": value, "until": self.clock() + minutes * 60}
            self._changed()

    def _active_multiplier(self, now):
        if self.multiplier["until"] > now:
            return float(self.multiplier["value"])
        return 1.0

    # --------------------------------------------------------------- settings
    @staticmethod
    def _clean_settings(patch: dict) -> dict:
        """Validasi semua nilai dulu; angka tidak valid -> ValueError tanpa mengubah apa pun.
        Nilai teks yang tidak dikenal (tema, warna, dll.) diabaikan."""
        clean = {}
        for k, v in patch.items():
            if k not in DEFAULTS:
                continue
            if k == "overlay_styles":
                if not isinstance(v, dict):
                    raise ValueError("Gaya per overlay tidak valid")
                clean[k] = {
                    name: Engine._clean_settings({sk: sv for sk, sv in style.items() if sk in STYLE_KEYS})
                    for name, style in v.items()
                    if name in OVERLAYS and isinstance(style, dict)
                }
                continue
            if k in FLOAT_KEYS:
                v = finite(v, LABELS.get(k, k))
                v = min(max(FLOAT_MIN.get(k, 0.0), v), FLOAT_MAX.get(k, 1e9))
            elif k in INT_KEYS:
                v = int(finite(v, LABELS.get(k, k)))
                if not 1 <= v <= 65535:
                    raise ValueError(f"{LABELS.get(k, k)}: harus 1–65535")
            elif k in BOOL_KEYS:
                v = bool(v)
            elif k in STR_KEYS:
                v = str(v).strip()[:300]
                if k == "accent" and not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                    continue
                if k == "font" and not re.fullmatch(r"[A-Za-z0-9 ,'\"_-]{1,120}", v):
                    continue
                if k in CHOICES and v not in CHOICES[k]:
                    continue
                if k == "unit_label":
                    v = v[:30] or DEFAULTS["unit_label"]
            clean[k] = v
        return clean

    def update_settings(self, patch: dict):
        clean = self._clean_settings(patch)
        with self.lock:
            old_initial = self.settings["initial_minutes"]
            self.settings.update(clean)
            if self.status == "idle":
                # Geser sebesar perubahan waktu awal saja, agar waktu dari event sebelum Mulai tidak hilang.
                now = self.clock()
                delta = (self.settings["initial_minutes"] - old_initial) * 60
                self._set_rem(min(self._cap(), self._remaining(now) + delta), now)
            self._changed()

    def public_settings(self):
        return {k: v for k, v in self.settings.items() if k not in SECRET_KEYS}

    # ------------------------------------------------------------------ goals
    def goals(self):
        rows = self.db.query("SELECT * FROM goals ORDER BY target ASC, id ASC")
        for g in rows:
            if g["manual_done"] is not None:
                g["done"] = bool(g["manual_done"])
            else:
                g["done"] = self.counter >= g["target"]
        return rows

    def add_goal(self, target, text, bonus_minutes=0.0, scene=""):
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO goals(target,text,bonus_minutes,scene) VALUES(?,?,?,?)",
                (
                    max(0, int(target)),
                    str(text)[:200],
                    max(0.0, finite(bonus_minutes, "Bonus menit")),
                    str(scene)[:100],
                ),
            )
            self._eval_goals(silent=True)
            self._changed()
            return cur.lastrowid

    def update_goal(self, goal_id, patch: dict):
        columns = {
            "target": lambda v: max(0, int(finite(v, "Target"))),
            "text": lambda v: str(v)[:200],
            "bonus_minutes": lambda v: max(0.0, finite(v, "Bonus menit")),
            "scene": lambda v: str(v)[:100],
            "manual_done": lambda v: None if v is None else int(bool(v)),
        }
        changes = {k: conv(patch[k]) for k, conv in columns.items() if k in patch}
        if not changes:
            return
        with self.lock:
            sets = ", ".join(f"{k}=?" for k in changes)
            self.db.execute(f"UPDATE goals SET {sets} WHERE id=?", (*changes.values(), int(goal_id)))
            self._eval_goals(silent=True)
            self._changed()

    def delete_goal(self, goal_id):
        with self.lock:
            self.db.execute("DELETE FROM goals WHERE id=?", (int(goal_id),))
            self._changed()

    def _eval_goals(self, silent=False):
        """Tandai goal yang baru tercapai, dan buka kembali yang tidak lagi tercapai setelah undo.
        silent=True (koreksi manual, goal baru) tidak memberi bonus waktu dan tidak memicu alert."""
        now = self.clock()
        reached = []
        for g in self.db.query("SELECT * FROM goals"):
            if g["reached_at"] is None and self.counter >= g["target"]:
                bonus = 0.0
                applied = g["bonus_applied"]
                if silent:
                    applied = 1
                elif g["bonus_minutes"] > 0 and not applied:
                    bonus = self._add_time(g["bonus_minutes"] * 60, now)
                    applied = 1
                self.db.execute("UPDATE goals SET reached_at=?, bonus_applied=? WHERE id=?", (now, applied, g["id"]))
                if not silent:
                    reached.append({**g, "bonus_seconds": bonus})
            elif g["reached_at"] is not None and self.counter < g["target"]:
                self.db.execute("UPDATE goals SET reached_at=NULL WHERE id=?", (g["id"],))
        return reached

    def _add_time(self, seconds, now):
        """Tambah waktu: diabaikan jika timer sudah selesai, dibatasi max_hours."""
        if self.status == "ended":
            return 0.0
        cur = self._remaining(now)
        new = min(self._cap(), cur + seconds)
        self._set_rem(new, now)
        return new - cur

    # ----------------------------------------------------------------- events
    def handle_event(self, ext_id, kind, author="", units=1, amount=None, currency=None, note=""):
        """Proses satu event. Mengembalikan dict event, atau None jika duplikat/tidak valid."""
        if kind not in ("member", "gift", "superchat"):
            return None
        units = max(0, int(units))
        with self.lock:
            if ext_id and self.db.query("SELECT 1 FROM events WHERE ext_id=?", (ext_id,)):
                return None
            now = self.clock()
            s = self.settings
            base = 0.0
            counter_delta = 0
            if kind in ("member", "gift"):
                units = max(1, units)
                counter_delta = units
                base = units * s["minutes_per_member" if kind == "member" else "minutes_per_gift"] * 60
            else:
                units = 0
                try:
                    amount = finite(amount) if amount is not None else None
                except ValueError:
                    amount = None
                step = s["superchat_amount_per_step"]
                if (currency or "").upper() == s["superchat_currency"].upper() and amount and amount > 0 and step > 0:
                    base = int(amount // step) * s["superchat_minutes_per_step"] * 60
                else:
                    note = (note + " " if note else "") + f"mata uang {currency} tidak dihitung"

            mult = self._active_multiplier(now)
            added = self._add_time(base * mult, now)
            if mult != 1.0:
                note = (note + " " if note else "") + f"x{fmt_num(mult)}"

            self.counter += counter_delta
            author = str(author)[:80]
            cur = self.db.execute(EVENT_INSERT, (now, ext_id, kind, author, units, counter_delta, added, note))
            event = {
                "id": cur.lastrowid,
                "ts": now,
                "kind": kind,
                "author": author,
                "units": units,
                "seconds_added": added,
                "note": note,
            }
            reached = self._eval_goals()
            self._changed()
        self.on_alert({"type": "event", **event})
        for g in reached:
            self.on_alert(
                {
                    "type": "goal",
                    "goal_id": g["id"],
                    "text": g["text"],
                    "target": g["target"],
                    "bonus_seconds": g["bonus_seconds"],
                }
            )
            self.on_goal(g)
        return event

    def undo(self, event_id):
        with self.lock:
            rows = self.db.query("SELECT * FROM events WHERE id=? AND undone=0", (int(event_id),))
            if not rows:
                return False
            ev = rows[0]
            now = self.clock()
            self.counter = max(0, self.counter - ev["counter_delta"])
            if self.status != "ended" and ev["seconds_added"]:
                self._set_rem(min(self._cap(), self._remaining(now) - ev["seconds_added"]), now)
            self.db.execute("UPDATE events SET undone=1 WHERE id=?", (ev["id"],))
            self._eval_goals(silent=True)
            self._changed()
            return True

    def set_counter(self, value):
        """Koreksi total manual (mis. setelah aplikasi sempat mati). Tidak memberi bonus waktu."""
        with self.lock:
            value = max(0, int(value))
            delta = value - self.counter
            if delta == 0:
                return
            self.counter = value
            self.db.execute(
                EVENT_INSERT, (self.clock(), None, "set", "admin", 0, delta, 0, f"hitungan diubah ke {value}")
            )
            self._eval_goals(silent=True)
            self._changed()

    # --------------------------------------------------------------- snapshot
    def rule_text(self):
        s = self.settings
        if s["rule_text"]:
            return s["rule_text"]
        text = f"1 Membership = {fmt_num(s['minutes_per_member'])} Menit"
        if s["minutes_per_gift"] != s["minutes_per_member"]:
            text += f" | 1 Gift = {fmt_num(s['minutes_per_gift'])} Menit"
        return text

    def snapshot(self):
        """Seluruh state yang dikirim ke Admin dan overlay lewat WebSocket."""
        with self.lock:
            now = self.clock()
            goals = self.goals()
            current = next((g for g in goals if not g["done"]), None)
            events = self.db.query(
                "SELECT id,ts,kind,author,units,seconds_added,note,undone FROM events ORDER BY id DESC LIMIT 40"
            )
            latest = [e for e in events if e["kind"] in COUNTED_KINDS and not e["undone"]][:15]
            gifters = self.db.query(
                "SELECT author, SUM(units) AS total FROM events "
                "WHERE kind='gift' AND undone=0 AND author<>'' GROUP BY author ORDER BY total DESC LIMIT 5"
            )
            mult = self._active_multiplier(now)
            return {
                "status": self.status,
                "remaining": self._remaining(now),
                "counter": self.counter,
                "multiplier": {
                    "value": mult,
                    "ends_in": max(0.0, self.multiplier["until"] - now) if mult != 1.0 else 0,
                },
                "settings": self.public_settings(),
                "rule_text": self.rule_text(),
                "goals": goals,
                "current_goal_id": current["id"] if current else None,
                "events": events,
                "latest_members": latest,
                "top_gifters": gifters,
            }
