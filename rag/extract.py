"""Lapis 1 (tahap 1): ekstraksi teks bawaan PDF per halaman + pemeriksaan kualitas.

Tidak memakai GPU. Halaman yang gagal pemeriksaan ditandai `needs_ocr` dan masuk
antrian OCR (tahap 2). Bisa dilanjutkan kalau terputus: file yang sudah selesai
(hash + versi parser sama) dilewati.

Pemakaian:  python -m rag.extract "<folder pustaka>"
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import pymupdf
from rich.console import Console
from rich.progress import (BarColumn, MofNCompleteColumn, Progress, TextColumn,
                           TimeElapsedColumn, TimeRemainingColumn)
from wordfreq import zipf_frequency

from . import PARSER_VERSION

# Nilai AWAL (SPEC §5.2): bukan hasil kalibrasi. Dikalibrasi nanti pada halaman berlabel.
MIN_CHARS = 200
MIN_DICT_RATIO = 0.85
MAX_GARBAGE = 0.02
FORMULA_DENSITY = 0.15

MATH_CHARS = set("=+−-×÷∑∏∫∂∇√∞≤≥≠≈∈∉⊂⊆∪∩∀∃→←↔⇒⇔αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ^_{}()[]|/<>")
GARBAGE_RE = re.compile(r"[�\u0000-\u0008\u000b\u000c\u000e-\u001f-]")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.I)
YEAR_RE = re.compile(r"(1[5-9]\d\d|20[0-4]\d)")
ROMAN_RE = re.compile(r"^(?=[ivxlcdm]+$)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$", re.I)
REF_HEAD_RE = re.compile(r"^\s*(references|bibliography|daftar pustaka|works cited|literature cited)\s*$", re.I | re.M)

console = Console()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dict_ratio(text: str) -> float | None:
    words = [w.lower().strip("'-") for w in WORD_RE.findall(text) if len(w) >= 3]
    if len(words) < 20:
        return None
    known = sum(1 for w in words if zipf_frequency(w, "en") > 1.0)
    return known / len(words)


def page_signals(text: str, n_images: int) -> dict:
    stripped = re.sub(r"\s+", "", text)
    n = len(stripped)
    garbage = len(GARBAGE_RE.findall(text)) / n if n else 0.0
    math = sum(1 for c in stripped if c in MATH_CHARS) / n if n else 0.0
    dr = dict_ratio(text)
    return {"chars": n, "dict_ratio": None if dr is None else round(dr, 3),
            "garbage": round(garbage, 4), "math_density": round(math, 3), "images": n_images}


def classify(text: str, sig: dict) -> str:
    if sig["chars"] < 20:
        return "figure" if sig["images"] else "blank"
    if REF_HEAD_RE.search(text) or len(re.findall(r"\(\d{4}[a-z]?\)|\[\d+\]", text)) > 15:
        return "references"
    if sig["math_density"] >= FORMULA_DENSITY:
        return "formula_heavy"
    if sig["chars"] < MIN_CHARS and sig["images"]:
        return "figure"
    return "prose"


def needs_ocr(ptype: str, sig: dict) -> list[str]:
    """Alasan halaman perlu OCR. Kosong = lolos."""
    if ptype == "blank":
        return []
    why = []
    if sig["chars"] < MIN_CHARS and ptype != "figure":
        why.append("teks_sedikit")
    if sig["garbage"] > MAX_GARBAGE:
        why.append("karakter_rusak")
    if ptype == "prose" and sig["dict_ratio"] is not None and sig["dict_ratio"] < MIN_DICT_RATIO:
        why.append("kata_tak_dikenal")
    if ptype == "figure" and sig["images"] and sig["chars"] < 20:
        why.append("halaman_gambar")
    return why


def page_number_candidates(edge_lines: list[str]) -> dict:
    """Semua kandidat nomor halaman dari baris header/footer (berdasarkan posisi y)."""
    arabic, roman = set(), set()
    for l in edge_lines:
        tok = re.split(r"[\s|·•—–\-]+", l.strip())
        for t in tok:
            if re.fullmatch(r"[0-9]{1,4}", t) and 0 < int(t) < 3000:   # ASCII saja ('³' bukan angka halaman)
                arabic.add(int(t))
        if len(l.strip()) <= 8 and ROMAN_RE.match(l.strip()):
            roman.add(l.strip().lower())
    return {"arabic": arabic, "roman": roman}


def resolve_printed_pages(cands: list[dict]) -> list[tuple[str | None, float]]:
    """Pilih offset (printed = pdf_page + offset) yang paling banyak didukung kandidat.
    Bisa berganti offset (mis. buku: front matter romawi lalu halaman 1)."""
    n = len(cands)
    votes = Counter(a - i for i, c in enumerate(cands, 1) for a in c["arabic"])
    pages_with_num = sum(1 for c in cands if c["arabic"]) or 1
    out: list[tuple[str | None, float]] = [(None, 0.0)] * n
    if not votes:
        return [(sorted(c["roman"])[0], 0.4) if c["roman"] else (None, 0.0) for c in cands]
    # ambil hingga 3 offset dominan (untuk buku dengan penomoran berganti)
    top = [o for o, v in votes.most_common(3) if v >= max(3, 0.15 * pages_with_num)]
    for i, c in enumerate(cands, 1):
        hit = [o for o in top if (i + o) in c["arabic"]]
        if hit:
            o = hit[0]
            support = votes[o] / pages_with_num
            out[i - 1] = (str(i + o), round(min(1.0, 0.6 + support * 0.4), 2))
        elif c["roman"]:
            out[i - 1] = (sorted(c["roman"])[0], 0.5)
    # isi celah: halaman di antara dua halaman ber-offset sama → disimpulkan
    known = [(i, int(pn) - (i + 1)) for i, (pn, cf) in enumerate(out) if pn and pn.isdecimal() and cf >= 0.6]
    for (a, oa), (b, ob) in zip(known, known[1:]):
        if oa == ob and b - a > 1:
            for j in range(a + 1, b):
                if out[j][0] is None:
                    out[j] = (str(j + 1 + oa), 0.75)
    return out


def edge_lines(page, k: int = 2) -> list[str]:
    """k baris teratas dan terbawah halaman menurut posisi y (header/footer)."""
    d = page.get_text("dict")
    lines = []
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b["lines"]:
            t = "".join(s["text"] for s in l["spans"]).strip()
            if t:
                lines.append((l["bbox"][1], t))
    lines.sort()
    h = page.rect.height or 1
    # baris di 10% atas/bawah halaman, ditambah k baris paling tepi
    band = [t for y, t in lines if y < 0.10 * h or y > 0.88 * h]
    edge = [t for _, t in lines[:k] + lines[-k:]]
    return list(dict.fromkeys(band + edge))


def extract_blocks(page) -> tuple[list[dict], str]:
    d = page.get_text("dict")
    spans_sizes = [s["size"] for b in d["blocks"] if b.get("type") == 0
                   for l in b["lines"] for s in l["spans"] if s["text"].strip()]
    body = statistics.median(spans_sizes) if spans_sizes else 10
    blocks, full = [], []
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        lines = ["".join(s["text"] for s in l["spans"]) for l in b["lines"]]
        text = "\n".join(lines).strip()
        if not text:
            continue
        text = re.sub(r"-\n(?=[a-z])", "", text)
        sizes = [s["size"] for l in b["lines"] for s in l["spans"] if s["text"].strip()]
        big = sizes and max(sizes) >= body * 1.2 and len(text) < 200
        blocks.append({"type": "heading" if big else "paragraph", "text": text,
                       "bbox": [round(x, 1) for x in b["bbox"]]})
        full.append(text)
    return blocks, "\n".join(full)


def process_pdf(pdf: Path, sha: str, out_dir: Path, qwriter) -> dict:
    doc = pymupdf.open(pdf)
    pages = []
    for i, page in enumerate(doc, 1):
        blocks, text = extract_blocks(page)
        sig = page_signals(text, len(page.get_images()))
        ptype = classify(text, sig)
        why = needs_ocr(ptype, sig)
        pages.append(dict(i=i, blocks=blocks, text=text, sig=sig, ptype=ptype, why=why,
                          raw_pn=page_number_candidates(edge_lines(page))))
    printed = resolve_printed_pages([p["raw_pn"] for p in pages])

    doi = None
    for p in pages[:2]:
        m = DOI_RE.search(p["text"])
        if m:
            doi = m.group(0).rstrip(".,;)")
            break

    tmp = out_dir / f"{sha}.jsonl.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for p, (pn, pconf) in zip(pages, printed):
            phash = hashlib.sha1(p["text"].encode("utf-8")).hexdigest()[:16]
            q = dict(p["sig"])
            for bi, b in enumerate(p["blocks"]):
                f.write(json.dumps({
                    "doc": sha, "pdf_page": p["i"], "printed_page": pn, "printed_page_conf": pconf,
                    "page_type": p["ptype"], "block": bi, "type": b["type"], "text": b["text"],
                    "bbox": b["bbox"], "engine": "pymupdf", "engine_version": pymupdf.VersionBind,
                    "extract_version": PARSER_VERSION, "page_hash": phash, "quality": q,
                    "needs_ocr": p["why"], "lang": "en"}, ensure_ascii=False) + "\n")
            qwriter.writerow([pdf.name, sha[:12], p["i"], pn, pconf, p["ptype"], q["chars"],
                              q["dict_ratio"], q["garbage"], q["math_density"], q["images"],
                              ";".join(p["why"])])
    tmp.replace(out_dir / f"{sha}.jsonl")

    n = len(pages)
    n_ocr = sum(1 for p in pages if p["why"])
    stem = pdf.stem
    years = YEAR_RE.findall(stem)
    meta = {
        "sha256": sha, "file": str(pdf.name), "rel_path": None, "pages": n,
        "title": (doc.metadata or {}).get("title") or stem.replace("_", " "),
        "author_meta": (doc.metadata or {}).get("author") or None,
        "year_guess": int(years[0]) if years else None, "doi": doi,
        "type_guess": "book" if n > 120 else "paper",
        "page_types": dict(Counter(p["ptype"] for p in pages)),
        "pages_need_ocr": n_ocr, "frac_need_ocr": round(n_ocr / n, 3) if n else 0,
        "printed_page_coverage": round(sum(1 for pn, _ in printed if pn) / n, 3) if n else 0,
        "duplicate_group": None, "retracted": "unknown",
        "extract_version": PARSER_VERSION, "extracted_at": datetime.now().isoformat(timespec="seconds"),
    }
    return meta


def main(folder: str) -> None:
    root = Path(folder).resolve()
    rag = root / ".rag"
    out_dir = rag / "extract"
    logs = rag / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    log = open(logs / "index.log", "a", encoding="utf-8")

    def say(msg: str, style: str = "") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        log.write(f"{stamp} {msg}\n"); log.flush()
        console.print(f"[dim]{stamp}[/] {msg}", style=style)

    pdfs = sorted(p for p in root.rglob("*.pdf") if ".rag" not in p.parts)
    queue_path = out_dir / "_queue.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else {}
    report_path = out_dir / "_quality_report.csv"
    new_report = not report_path.exists()
    rep = open(report_path, "a", newline="", encoding="utf-8")
    qw = csv.writer(rep)
    if new_report:
        qw.writerow(["file", "sha", "pdf_page", "printed_page", "printed_conf", "page_type", "chars",
                     "dict_ratio", "garbage", "math_density", "images", "needs_ocr"])

    say(f"Mulai ekstraksi tahap 1 (CPU, tanpa GPU): {len(pdfs)} PDF di {root}", "bold")
    seen: dict[str, str] = {}
    done = skipped = failed = 0
    t0 = time.time()
    with Progress(TextColumn("[bold]{task.description}"), BarColumn(), MofNCompleteColumn(),
                  TextColumn("·"), TimeElapsedColumn(), TextColumn("· sisa"), TimeRemainingColumn(),
                  console=console) as prog:
        task = prog.add_task("Ekstraksi", total=len(pdfs))
        for pdf in pdfs:
            prog.update(task, description=f"{pdf.name[:45]:45}")
            sha = None
            try:
                sha = sha256_file(pdf)
                rel = str(pdf.relative_to(root))
                if sha in seen:
                    say(f"[DUPLIKAT] {rel} identik dengan {seen[sha]}", "yellow")
                    queue.setdefault(sha, {}).setdefault("aliases", []).append(rel)
                    skipped += 1
                    continue
                seen[sha] = rel
                q = queue.get(sha, {})
                if q.get("status") == "extracted" and q.get("version") == PARSER_VERSION \
                        and (out_dir / f"{sha}.jsonl").exists():
                    skipped += 1
                    continue
                meta = process_pdf(pdf, sha, out_dir, qw)
                meta["rel_path"] = rel
                (out_dir / f"{sha}.meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
                queue[sha] = {"file": rel, "status": "extracted", "version": PARSER_VERSION,
                              "pages": meta["pages"], "pages_need_ocr": meta["pages_need_ocr"]}
                queue_path.write_text(json.dumps(queue, indent=1, ensure_ascii=False), encoding="utf-8")
                rep.flush()
                flag = f"  [!] {meta['pages_need_ocr']} halaman perlu OCR" if meta["pages_need_ocr"] else ""
                say(f"[OK] {rel}: {meta['pages']} hlm{flag}", "yellow" if flag else "")
                done += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                queue[sha or pdf.name] ={"file": pdf.name, "status": "error", "error": str(e)[:200]}
                say(f"[GAGAL] {pdf.name}: {e}. Saran: buka PDF-nya; mungkin rusak atau terkunci.", "red")
            finally:
                prog.advance(task)

    queue_path.write_text(json.dumps(queue, indent=1, ensure_ascii=False), encoding="utf-8")
    tot_pages = sum(v.get("pages", 0) for v in queue.values())
    tot_ocr = sum(v.get("pages_need_ocr", 0) for v in queue.values())
    say(f"SELESAI dalam {time.time()-t0:.0f} dtk · diproses {done}, dilewati {skipped}, gagal {failed} · "
        f"{tot_pages} halaman, {tot_ocr} perlu OCR (tahap 2, butuh GPU)", "bold green")
    rep.close(); log.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__); sys.exit(1)
    main(sys.argv[1])
