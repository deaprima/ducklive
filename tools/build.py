"""Build paket Windows: aplikasi (PyInstaller, tanpa jendela konsol) + installer (Inno Setup) + zip portable.

Pakai:  python tools/build.py [versi]      (mis. python tools/build.py 1.2.0)
Butuh:  pip install -r requirements.txt pyinstaller
        Inno Setup 6 (https://jrsoftware.org/isdl.php) untuk membuat installer; tanpa itu hanya zip portable.
Hasil:  dist/DuckLive-Setup-<versi>.exe dan dist/DuckLive-Portable-<versi>.zip
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP = DIST / "DuckLive"
# Modul uvicorn yang dimuat dinamis sehingga tidak terdeteksi PyInstaller.
HIDDEN_IMPORTS = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]


def find_iscc():
    for p in (
        shutil.which("iscc"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
    ):
        if p and Path(p).exists():
            return p
    return None


def main():
    version = (sys.argv[1] if len(sys.argv) > 1 else "0.0.0").lstrip("v")
    shutil.rmtree(APP, ignore_errors=True)
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed"]
    args += ["--name", "DuckLive", "--icon", str(ROOT / "assets" / "icon.ico")]
    args += ["--add-data", f"{ROOT / 'web'}{os.pathsep}web", "--collect-all", "chat_downloader"]
    for module in HIDDEN_IMPORTS:
        args += ["--hidden-import", module]
    args += ["--distpath", str(DIST), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build")]
    subprocess.run([*args, str(ROOT / "run.py")], check=True, cwd=ROOT)

    portable = DIST / f"DuckLive-Portable-{version}"
    shutil.make_archive(str(portable), "zip", DIST, "DuckLive")
    print(f"OK  {portable}.zip")

    iscc = find_iscc()
    if not iscc:
        print("Inno Setup tidak ditemukan: installer dilewati (zip portable tetap dibuat).")
        return
    subprocess.run([iscc, f"/DAppVersion={version}", f"/O{DIST}", str(ROOT / "installer" / "DuckLive.iss")], check=True)
    print(f"OK  {DIST / f'DuckLive-Setup-{version}.exe'}")


if __name__ == "__main__":
    main()
