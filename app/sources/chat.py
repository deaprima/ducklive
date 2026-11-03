"""Pesan live chat untuk overlay chat (hanya di memori, tidak disimpan ke database).

Sumber memanggil chat(op) dengan salah satu bentuk:
  {"op": "add", "item": {...}}       pesan baru (lihat make_item)
  {"op": "delete", "id": "..."}      pesan dihapus moderator
  {"op": "ban", "channel": "..."}    semua pesan dari satu penonton dihapus (ban/timeout)
"""

MAX_TEXT = 300  # karakter per pesan; chat YouTube sendiri dibatasi 200
MAX_PARTS = 60
# Foto profil, lencana member, dan emoji kustom hanya boleh dimuat dari server gambar YouTube.
IMAGE_HOSTS = ("https://yt3.ggpht.com/", "https://yt4.ggpht.com/", "https://yt3.googleusercontent.com/")
ROLES = {"owner", "moderator", "member", "verified", ""}


def safe_image_url(url) -> bool:
    return isinstance(url, str) and url.startswith(IMAGE_HOSTS) and len(url) < 1000


def pick_image(images, want=64) -> str:
    """URL gambar terkecil yang lebarnya >= want (atau yang terbesar) dari daftar {url, width}."""
    sized = [i for i in images or [] if isinstance(i, dict) and i.get("url") and i.get("width")]
    if not sized:
        return next((i["url"] for i in images or [] if isinstance(i, dict) and i.get("url")), "")
    big = [i for i in sized if i["width"] >= want]
    best = min(big, key=lambda i: i["width"]) if big else max(sized, key=lambda i: i["width"])
    return best["url"]


def clip_parts(parts) -> list[dict]:
    """Batasi panjang teks dan jumlah bagian; buang bagian yang bentuknya tidak dikenal."""
    out, left = [], MAX_TEXT
    for p in parts or []:
        if left <= 0 or len(out) >= MAX_PARTS:
            break
        if not isinstance(p, dict):
            continue
        if isinstance(p.get("t"), str) and p["t"]:
            text = p["t"][:left]
            left -= len(text)
            out.append({"t": text})
        elif safe_image_url(p.get("img")):
            out.append({"img": p["img"], "alt": str(p.get("alt") or "")[:40]})
            left -= 1
    return out


def make_item(msg_id, kind, author, channel="", role="", parts=None, amount=None, avatar="", badge="") -> dict:
    return {
        "id": str(msg_id),
        "kind": kind,  # "text" | "superchat" | "member" | "gift"
        "author": str(author or "")[:80],
        "channel": str(channel or "")[:100],
        "role": role if role in ROLES else "",
        "parts": clip_parts(parts),
        "amount": str(amount)[:40] if amount else "",
        "avatar": avatar if safe_image_url(avatar) else "",
        "badge": badge if safe_image_url(badge) else "",  # gambar lencana member channel
    }
