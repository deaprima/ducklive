"""Build paket Windows: aplikasi (PyInstaller, tanpa jendela konsol) + installer (Inno Setup) + zip portable.

Pakai:  python tools/build.py [versi]
        Tanpa argumen: versi dari app/__init__.py. Di GitHub Actions, build dari tag vX.Y.Z harus
        sama dengan versi di kode; build manual diberi akhiran "-dev".
Butuh:  pip install -r requirements.txt pyinstaller
        Inno Setup 6+ (https://jrsoftware.org/isdl.php) untuk membuat installer; tanpa itu hanya zip portable
        (kecuali di CI, yang gagal jika installer tidak bisa dibuat).
Hasil:  dist/DuckLive-Setup-<versi>.exe dan dist/DuckLive-Portable-<versi>.zip
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP = DIST / "DuckLive"
# Modul uvicorn yang dimuat dinamis sehingga tidak terdeteksi PyInstaller.
HIDDEN_IMPORTS = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]


def code_version() -> str:
    text = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def resolve_version() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1].lstrip("v")
    version = code_version()
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag = os.environ.get("GITHUB_REF_NAME", "").lstrip("v")
        if tag != version:
            sys.exit(f"ERROR: tag v{tag} tidak sama dengan __version__ = {version} di app/__init__.py.")
    elif os.environ.get("CI"):
        version += "-dev"
    return version


def numeric_version(version: str) -> str:
    """'1.2.0-dev' -> '1.2.0.0' (Windows hanya menerima 4 angka)."""
    nums = [int(n) for n in re.findall(r"\d+", version.split("-")[0])][:4]
    return ".".join(str(n) for n in nums + [0] * (4 - len(nums)))


def write_version_file(version: str) -> Path:
    """Info versi Windows untuk DuckLive.exe (Properties > Details)."""
    nums = tuple(int(n) for n in numeric_version(version).split("."))
    strings = {
        "CompanyName": "DuckLive",
        "FileDescription": "DuckLive",
        "FileVersion": version,
        "InternalName": "DuckLive",
        "LegalCopyright": "MIT License",
        "OriginalFilename": "DuckLive.exe",
        "ProductName": "DuckLive",
        "ProductVersion": version,
    }
    entries = ", ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings.items())
    BUILD.mkdir(exist_ok=True)
    path = BUILD / "version_info.txt"
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [{entries}])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
""",
        encoding="utf-8",
    )
    return path


def find_iscc():
    if shutil.which("iscc"):
        return shutil.which("iscc")
    bases = [Path(os.environ[v]) for v in ("ProgramFiles(x86)", "ProgramFiles") if os.environ.get(v)]
    if os.environ.get("LOCALAPPDATA"):
        bases.append(Path(os.environ["LOCALAPPDATA"]) / "Programs")
    for base in bases:
        found = sorted(base.glob("Inno Setup */ISCC.exe"), reverse=True)  # versi terbaru dulu
        if found:
            return str(found[0])
    return None


def main():
    version = resolve_version()
    print(f"Versi: {version} ({numeric_version(version)})")
    shutil.rmtree(APP, ignore_errors=True)
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed"]
    args += ["--name", "DuckLive", "--icon", str(ROOT / "assets" / "icon.ico")]
    args += ["--version-file", str(write_version_file(version))]
    args += ["--add-data", f"{ROOT / 'web'}{os.pathsep}web", "--collect-all", "chat_downloader"]
    for module in HIDDEN_IMPORTS:
        args += ["--hidden-import", module]
    args += ["--distpath", str(DIST), "--workpath", str(BUILD), "--specpath", str(BUILD)]
    subprocess.run([*args, str(ROOT / "run.py")], check=True, cwd=ROOT)

    portable = DIST / f"DuckLive-Portable-{version}"
    shutil.make_archive(str(portable), "zip", DIST, "DuckLive")
    print(f"OK  {portable}.zip")

    iscc = find_iscc()
    if not iscc:
        # Di CI installer wajib ada; di komputer developer cukup zip portable.
        if os.environ.get("CI"):
            sys.exit("ERROR: Inno Setup (ISCC.exe) tidak ditemukan, installer tidak bisa dibuat.")
        print("Inno Setup tidak ditemukan: installer dilewati (zip portable tetap dibuat).")
        return
    print(f"Inno Setup: {iscc}")
    defines = [f"/DAppVersion={version}", f"/DAppVersionNumeric={numeric_version(version)}"]
    subprocess.run([iscc, *defines, f"/O{DIST}", str(ROOT / "installer" / "DuckLive.iss")], check=True)
    setup = DIST / f"DuckLive-Setup-{version}.exe"
    if not setup.exists():
        sys.exit(f"ERROR: Inno Setup selesai tetapi {setup.name} tidak ada.")
    print(f"OK  {setup}")


if __name__ == "__main__":
    main()
