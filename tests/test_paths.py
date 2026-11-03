from app.paths import _migrate


def test_migrates_legacy_data(tmp_path):
    legacy, new = tmp_path / "SubathonTimer", tmp_path / "DuckLive"
    legacy.mkdir()
    (legacy / "admin_token.txt").write_text("abc")
    assert _migrate(legacy, new) == new
    assert (new / "admin_token.txt").read_text() == "abc" and not legacy.exists()


def test_keeps_new_data_when_both_exist(tmp_path):
    legacy, new = tmp_path / "subathon.db", tmp_path / "ducklive.db"
    legacy.write_text("lama")
    new.write_text("baru")
    assert _migrate(legacy, new) == new
    assert new.read_text() == "baru" and legacy.exists()


def test_fresh_install(tmp_path):
    assert _migrate(tmp_path / "tidak-ada", tmp_path / "DuckLive") == tmp_path / "DuckLive"
