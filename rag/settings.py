"""Pengaturan per folder pustaka (SPEC §16, diputuskan 2026-11-18): .rag/settings.json

- private: True  -> server hanya mengembalikan metadata (file, judul, halaman); teks TIDAK dikirim ke Claude.
           Catatan: nama file, judul, dan kata pencarian tetap terkirim.
- lookup:  "doi" -> metadata + status retraction dari OpenAlex HANYA lewat DOI (lookup judul 0/15 benar, uji 2026-11-18)
           "off" -> tidak ada yang dikirim ke OpenAlex.
Tidak ada nilai bawaan: folder baru wajib diputuskan. Selama belum diputuskan, server memperlakukannya
sebagai private (aman) dan memberi tahu.

Pemakaian:  python -m rag.settings "<folder>"                       (tanya interaktif)
            python -m rag.settings "<folder>" --private no --lookup doi
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

GUIDE = """Pedoman private (jawab ya bila folder berisi salah satu):
  - naskah sendiri yang belum terbit / skripsi yang sedang diuji
  - draf atau bahan rahasia dari orang lain (pembimbing, reviewer)
  - bahan berlisensi yang melarang dibagikan ke pihak ketiga
Paper/buku terbit (termasuk medis) tidak perlu private. Data pasien asli JANGAN dimasukkan ke pustaka."""


def path(root: Path) -> Path:
    return Path(root) / ".rag" / "settings.json"


def load(root: Path) -> dict | None:
    try:
        return json.loads(path(root).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def is_private(root: Path) -> bool:
    s = load(root)
    return True if s is None else bool(s.get("private"))


def save(root: Path, private: bool, lookup: str) -> dict:
    assert lookup in ("doi", "off")
    s = {"private": bool(private), "lookup": lookup, "decided_at": datetime.now().isoformat(timespec="seconds"),
         "basis": "SPEC §16 (2026-11-18)"}
    path(root).parent.mkdir(parents=True, exist_ok=True)
    path(root).write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
    return s


def _yn(prompt: str) -> bool:
    while True:
        a = input(prompt).strip().lower()
        if a in ("y", "ya", "yes"):
            return True
        if a in ("n", "no", "tidak", "t"):
            return False


def ask(root: Path) -> dict | None:
    """Tanya pengguna bila belum diputuskan. Non-interaktif -> None (server menganggap private)."""
    s = load(root)
    if s is not None:
        return s
    if not sys.stdin.isatty():
        print(f"[!] Pengaturan privasi folder '{Path(root).name}' BELUM diputuskan -> dianggap private. "
              f"Jalankan: python -m rag.settings \"{root}\"")
        return None
    print(f"\nFolder baru: {root}\n{GUIDE}\n")
    priv = _yn("Jadikan folder ini PRIVATE? (ya/tidak): ")
    look = "off" if priv else ("doi" if _yn("Nyalakan lookup DOI ke OpenAlex (hanya DOI yang dikirim)? (ya/tidak): ") else "off")
    s = save(root, priv, look)
    print(f"Tersimpan: {path(root)} -> {s}")
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--private", choices=["yes", "no"])
    ap.add_argument("--lookup", choices=["doi", "off"])
    a = ap.parse_args()
    root = Path(a.folder).resolve()
    if a.private:
        print(save(root, a.private == "yes", a.lookup or ("off" if a.private == "yes" else "doi")))
    else:
        if path(root).exists():
            path(root).unlink()
        ask(root)


if __name__ == "__main__":
    main()
