import pytest

from app.db import DB
from app.engine import Engine


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    db = DB(tmp_path / "t.db")
    alerts, goals = [], []
    eng = Engine(db, clock=clock, on_alert=alerts.append, on_goal=goals.append)
    eng.update_settings({"initial_minutes": 60, "minutes_per_member": 3, "minutes_per_gift": 3, "max_hours": 100})
    return eng, clock, db, alerts, goals


def test_member_adds_time_and_counter(env):
    eng, clock, *_ = env
    eng.start()
    clock.t += 10
    eng.handle_event("a", "member", "Budi")
    assert eng.counter == 1
    assert eng.remaining() == pytest.approx(3600 - 10 + 180)


def test_gift_counts_per_gift(env):
    eng, *_ = env
    eng.start()
    eng.handle_event("g1", "gift", "Sari", units=5)
    assert eng.counter == 5
    assert eng.remaining() == pytest.approx(3600 + 5 * 180)


def test_dedupe_ext_id(env):
    eng, *_ = env
    eng.start()
    assert eng.handle_event("x", "member", "A") is not None
    assert eng.handle_event("x", "member", "A") is None
    assert eng.counter == 1


def test_pause_resume(env):
    eng, clock, *_ = env
    eng.start()
    clock.t += 100
    eng.pause()
    clock.t += 500
    assert eng.remaining() == pytest.approx(3500)
    eng.resume()
    clock.t += 50
    assert eng.remaining() == pytest.approx(3450)


def test_max_cap(env):
    eng, *_ = env
    eng.update_settings({"max_hours": 1.0})
    eng.start()
    ev = eng.handle_event("a", "gift", "A", units=100)
    assert eng.remaining() == pytest.approx(3600)
    assert ev["seconds_added"] == pytest.approx(0)
    assert eng.counter == 100  # hitungan tetap bertambah walau waktu penuh


def test_ended_ignores_time_but_counts(env):
    eng, clock, *_ = env
    eng.start()
    clock.t += 4000
    assert eng.tick() is True
    assert eng.status == "ended"
    ev = eng.handle_event("a", "member", "A")
    assert ev["seconds_added"] == 0
    assert eng.counter == 1


def test_undo_reverses_time_and_counter(env):
    eng, *_ = env
    eng.start()
    ev = eng.handle_event("a", "gift", "A", units=4)
    assert eng.undo(ev["id"]) is True
    assert eng.counter == 0
    assert eng.remaining() == pytest.approx(3600)
    assert eng.undo(ev["id"]) is False


def test_goals_reached_bonus(env):
    eng, clock, db, alerts, goals = env
    eng.add_goal(3, "Panti asuhan", bonus_minutes=10)
    eng.add_goal(10, "Lagu rap")
    eng.start()
    clock.t += 120
    eng.handle_event("a", "gift", "A", units=3)
    assert goals and goals[0]["text"] == "Panti asuhan"
    # 3600 - 120 + 3*180 + 600 bonus
    assert eng.remaining() == pytest.approx(3600 - 120 + 540 + 600)
    snap = eng.snapshot()
    g = snap["goals"][0]
    assert g["done"]
    assert snap["current_goal_id"] == snap["goals"][1]["id"]


def test_bonus_only_once_after_undo(env):
    eng, *_ = env
    eng.add_goal(2, "G", bonus_minutes=5)
    eng.start()
    e1 = eng.handle_event("a", "gift", "A", units=2)
    t1 = eng.remaining()
    eng.undo(e1["id"])
    eng.handle_event("b", "gift", "A", units=2)
    # bonus 5 menit dari pencapaian pertama tetap ada (undo tidak menariknya),
    # dan tidak diberikan lagi saat goal tercapai kedua kali
    assert t1 == pytest.approx(3600 + 360 + 300)
    assert eng.remaining() == pytest.approx(3600 + 360 + 300)


def test_multiplier(env):
    eng, *_ = env
    eng.start()
    eng.set_multiplier(2, 10)
    ev = eng.handle_event("a", "member", "A")
    assert ev["seconds_added"] == pytest.approx(360)


def test_superchat_rule(env):
    eng, *_ = env
    eng.start()
    ev = eng.handle_event("s1", "superchat", "A", amount=25000, currency="IDR")
    assert ev["seconds_added"] == pytest.approx(2 * 60)
    assert eng.counter == 0
    ev = eng.handle_event("s2", "superchat", "A", amount=50, currency="USD")
    assert ev["seconds_added"] == 0


def test_persist_restores_paused(env, tmp_path):
    eng, clock, db, *_ = env
    eng.start()
    clock.t += 100
    eng.handle_event("a", "member", "A")
    eng.persist()
    eng2 = Engine(db, clock=clock)
    assert eng2.status == "paused"
    assert eng2.counter == 1
    assert eng2.remaining() == pytest.approx(3600 - 100 + 180)


def test_set_counter_silent_no_bonus(env):
    eng, *_ = env
    eng.add_goal(5, "G", bonus_minutes=10)
    eng.start()
    eng.set_counter(7)
    assert eng.remaining() == pytest.approx(3600)
    assert eng.snapshot()["goals"][0]["done"]


def test_settings_validation(env):
    eng, *_ = env
    eng.update_settings({"accent": "javascript:x", "theme": "evil", "font": "a;}</style>", "minutes_per_member": -5})
    s = eng.settings
    assert s["accent"] == "#f4a93c" and s["theme"] == "dark"
    assert "<" not in s["font"] and s["minutes_per_member"] == 0
    assert "obs_password" not in eng.public_settings()


def test_settings_change_while_idle_keeps_time_from_early_events(env):
    # Bug lama: Sambungkan -> event masuk sebelum Mulai -> simpan pengaturan -> waktu event hilang.
    eng, *_ = env
    eng.handle_event("a", "member", "A")
    assert eng.remaining() == pytest.approx(3600 + 180)
    eng.update_settings({"goals_title": "BARU"})
    assert eng.remaining() == pytest.approx(3600 + 180)
    eng.update_settings({"initial_minutes": 90})
    assert eng.remaining() == pytest.approx(5400 + 180)


def test_invalid_numbers_rejected_atomically(env):
    eng, *_ = env
    for bad in ("", "abc", "nan", "inf", None):
        with pytest.raises(ValueError):
            eng.update_settings({"goals_title": "X", "minutes_per_member": bad})
    assert eng.settings["goals_title"] == "IMPACTHON" and eng.settings["minutes_per_member"] == 3
    with pytest.raises(ValueError):
        eng.adjust_seconds(float("nan"))
    with pytest.raises(ValueError):
        eng.set_multiplier(2, float("inf"))
    with pytest.raises(ValueError):
        eng.update_settings({"obs_port": 70000})
    eng.update_settings({"max_hours": 1e300, "api_poll_seconds": 1})
    assert eng.settings["max_hours"] == 10_000 and eng.settings["api_poll_seconds"] == 5


def test_superchat_bad_amount_ignored(env):
    eng, *_ = env
    eng.start()
    ev = eng.handle_event("s", "superchat", "A", amount=float("inf"), currency="IDR")
    assert ev["seconds_added"] == 0


def test_undo_negative_adjust_respects_cap(env):
    eng, *_ = env
    eng.update_settings({"max_hours": 1})
    eng.start()
    eng.adjust_seconds(-600)
    eng.handle_event("a", "gift", "A", units=10)  # mengisi sampai batas 1 jam
    manual = [e for e in eng.snapshot()["events"] if e["kind"] == "manual"][0]
    eng.undo(manual["id"])
    assert eng.remaining() == pytest.approx(3600)


def test_old_default_accent_migrates_to_brand(tmp_path):
    db = DB(tmp_path / "m.db")
    db.kv_set("settings", {"accent": "#e53935", "goals_title": "LAMA"})
    eng = Engine(db)
    assert eng.settings["accent"] == "#f4a93c" and eng.settings["goals_title"] == "LAMA"
    db.kv_set("settings", {"accent": "#123456"})
    assert Engine(db).settings["accent"] == "#123456"  # warna pilihan user tidak diubah


def test_overlay_style_settings_are_clamped(env):
    eng, *_ = env
    eng.update_settings(
        {
            "overlay_scale": 500,
            "card_opacity": -10,
            "corner_radius": 99,
            "align": "diagonal",
            "card_border": False,
            "text_shadow": 0,
        }
    )
    s = eng.settings
    assert s["overlay_scale"] == 200 and s["card_opacity"] == 0 and s["corner_radius"] == 40
    assert s["align"] == "center" and s["card_border"] is False and s["text_shadow"] is False
    eng.update_settings({"overlay_scale": 10, "align": "right"})
    assert eng.settings["overlay_scale"] == 50 and eng.settings["align"] == "right"


def test_overlay_styles_per_overlay(env):
    eng, *_ = env
    eng.update_settings(
        {
            "overlay_styles": {
                "goals": {"theme": "transparent", "card_opacity": 500, "title": "bukan gaya", "accent": "bukan-warna"},
                "timer": {"overlay_scale": 150, "align": "left"},
                "admin": {"theme": "light"},  # bukan overlay: dibuang
                "ticker": "rusak",  # bukan objek: dibuang
            }
        }
    )
    st = eng.settings["overlay_styles"]
    assert st == {
        "goals": {"theme": "transparent", "card_opacity": 100},
        "timer": {"overlay_scale": 150, "align": "left"},
    }
    assert eng.public_settings()["overlay_styles"]["goals"]["theme"] == "transparent"
    with pytest.raises(ValueError):
        eng.update_settings({"overlay_styles": "transparan"})
    with pytest.raises(ValueError):
        eng.update_settings({"overlay_styles": {"timer": {"overlay_scale": "abc"}}})
    eng.update_settings({"overlay_styles": {}})
    assert eng.settings["overlay_styles"] == {}
