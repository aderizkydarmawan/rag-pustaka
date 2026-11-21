"""Deteksi keluaran OCR yang macet berulang (termasuk pola berhitung seperti p_{78} p_{79} ...).

Perbaikan 2026-11-17:
- dulu hanya 1.500 karakter terakhir yang dipotong, sehingga pengulangan yang lebih panjang tetap
  tersimpan (mis. Feller hlm 60: ±11 ribu karakter "α β β β"); sekarang dipotong berulang sampai bersih;
- deret angka panjang ("2.0000…", "174 174 …") tidak terlihat di kerangka (angka → #); sekarang
  teks asli juga diperiksa.
"""
import re

_REP = re.compile(r"(.{3,80}?)(?:\s*\1){7,}", re.S)
_CHAR_RUN = re.compile(r"(.)\1{59,}$", re.S)          # satu karakter berulang ≥60× di ujung teks
MARK = "\n[…keluaran OCR berulang dipotong…]"


def skeleton(s: str) -> str:
    return re.sub(r"\d+", "#", s)


def _tail_loop(t: str) -> int | None:
    for m in _REP.finditer(t):
        if m.end() >= len(t) - 40 and (m.end() - m.start()) >= 120:
            return m.start()
    return None


def loop_start(text: str, tail: int = 1500) -> int | None:
    """Indeks awal pengulangan degeneratif di bagian akhir teks, atau None."""
    t = text[-tail:]
    off = len(text) - len(t)
    m = _CHAR_RUN.search(t)
    if m:
        return off + m.start()
    i = _tail_loop(t)                      # teks asli (menangkap deret angka)
    if i is not None:
        return off + i
    sk = skeleton(t)                       # kerangka (menangkap pola berhitung)
    b = _tail_loop(sk)
    if b is None:
        return None
    pos = [m.start() for m in re.finditer(r"\d+|.", t, re.S)]
    toks = re.findall(r"#|.", sk[:b], re.S)
    return off + (pos[len(toks)] if len(toks) < len(pos) else 0)


def trim(text: str) -> tuple[str, bool]:
    if text.endswith(MARK):
        text = text[: -len(MARK)]
    cut = False
    for _ in range(500):                   # potong per jendela sampai ujung teks bersih
        i = loop_start(text)
        if i is None:
            break
        text, cut = text[:i].rstrip(), True
    return (text + MARK, True) if cut else (text, False)
