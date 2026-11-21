"""Lapis 2 (versi CPU): chunk + tabel halaman + indeks kata kunci (BM25/FTS) di LanceDB.

Dibangun ulang dari lapis 1 (`.rag/extract`) tanpa OCR ulang.
Embedding (pencarian makna) ditambahkan nanti di sesi GPU.

Pemakaian:  python -m rag.library build "<folder pustaka>"
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import re
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import lancedb
import pyarrow as pa
from rich.console import Console

from . import PARSER_VERSION
from . import enrich as _enrich
from . import settings as _settings
from .extract import page_number_candidates, resolve_printed_pages

CHUNKER_VERSION = "struct-words-2"   # 2: buang header/footer berulang, heading OCR, nomor halaman OCR
TARGET_WORDS = 350          # ≈ 450–500 token tokenizer Qwen3 (nilai awal, SPEC §6.1)
MAX_WORDS = 600
OVERLAP_BLOCKS = 1
REGISTRY = Path.home() / ".rag" / "libraries.json"
console = Console()


def find_library(start: str | os.PathLike | None) -> Path | None:
    """Cari folder ber-.rag dari `start` naik ke atas, lalu ke bawah 2 tingkat."""
    if not start:
        return None
    p = Path(start).resolve()
    for q in [p, *p.parents]:
        if (q / ".rag" / "index" / "lancedb").exists():
            return q
    for depth in ("*", "*/*"):
        for q in sorted(p.glob(depth)):
            if (q / ".rag" / "index" / "lancedb").exists():
                return q
    return None


def registry() -> dict:
    try:
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def register(name: str, path: Path) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    reg = registry()
    reg[name] = str(path)
    REGISTRY.write_text(json.dumps(reg, indent=1, ensure_ascii=False), encoding="utf-8")


_JUNK_TITLE = re.compile(r"\.(indd|dvi|docx?|tex|pdf|ps|qxd|eps)\b|microsoft word|^untitled|^pii:|djvu|^doi:|^arxiv|flip ?pdf|fasttrack|^front matter|^an introduction$|^\d|\bdocument$|^title$|^slides?\b|^lecture\s*\d|^chapter\s*\d|^\S+$|^[A-Z0-9_\-]{6,}$", re.I)
_FILE_TAGS = re.compile(r"^(RESENSI|KUNCI_JAWABAN)_(\d+hlm_)?|_(PREVIEW|BUKAN_BUKUNYA)(_\w*)?$|_\d+hlm(_dari_\d+)?", re.I)


def display_title(meta: dict, oa: dict | None) -> tuple[str, str]:
    """Judul untuk TAMPILAN (kartu, daftar, hasil pencarian, reranker), bukan untuk embedding.
    1) judul OpenAlex bila DOI cocok; 2) judul metadata PDF bila wajar; 3) nama file yang dirapikan."""
    if oa and oa.get("status") == "ok" and oa.get("title"):
        return oa["title"], "OpenAlex (DOI)"
    t = (meta.get("title") or "").strip()
    stem = Path(meta["file"]).stem
    t = re.sub(r"<[^>]+>", "", t)
    if t and not _JUNK_TITLE.search(t) and len(t.split()) >= 2 and t.replace(" ", "_") != stem:
        return t, "metadata PDF"
    x = _FILE_TAGS.sub("", stem).replace("_", " ")
    return re.sub(r"\s+", " ", x).strip(), "nama file"


def iter_docs(extract_dir: Path):
    for meta_path in sorted(extract_dir.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        jl = extract_dir / f"{meta['sha256']}.jsonl"
        if not jl.exists():
            continue
        blocks = [json.loads(l) for l in open(jl, encoding="utf-8")]
        blocks = merge_ocr(extract_dir, meta, blocks)
        blocks = fix_printed_pages(clean_doc(blocks, meta["pages"]), meta["pages"])
        yield meta, blocks


HEAD_RE = re.compile(r"^(?:#{1,4}\s*)?(?:(?:chapter|lecture|section|appendix|part)\b|\d{1,2}(?:\.\d{1,2}){0,3}\.?\s+[A-Z])", re.I)


def _ocr_heading(t: str) -> bool:
    """Paragraf OCR satu baris pendek yang tampak seperti judul bagian (mis. '2.8 Significant Figures')."""
    t = t.strip()
    return ("\n" not in t and 3 <= len(t) <= 100 and len(t.split()) <= 12 and not t.endswith((".", ",", ";", ":"))
            and "\\" not in t and "=" not in t and bool(HEAD_RE.match(t)))


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\b(?:[0-9]+|[ivxlc]+)\b", "#", t.lower())).strip()


def clean_doc(blocks: list[dict], n_pages: int) -> list[dict]:
    """Tandai header/footer/watermark berulang (boilerplate) dan heading tanpa huruf."""
    pages_of: dict[str, set] = {}
    for b in blocks:
        if len(b["text"]) < 200:
            pages_of.setdefault(_norm(b["text"]), set()).add(b["pdf_page"])
    thr = max(4, int(0.08 * n_pages))
    for b in blocks:
        b["boiler"] = len(b["text"]) < 200 and len(pages_of.get(_norm(b["text"]), ())) >= thr
        if b["type"] == "heading" and (b["boiler"] or sum(ch.isalpha() for ch in b["text"]) < 3):
            b["type"] = "paragraph"
    return blocks


def fix_printed_pages(blocks: list[dict], n_pages: int) -> list[dict]:
    """Isi nomor halaman cetak yang kosong (terutama halaman OCR) dari baris tepi teks + voting offset."""
    by_page: dict[int, list[dict]] = {}
    for b in blocks:
        by_page.setdefault(b["pdf_page"], []).append(b)
    if all(bl[0].get("printed_page") for bl in by_page.values()):
        return blocks
    cands = []
    for pg in range(1, n_pages + 1):
        bl = by_page.get(pg, [])
        pn = bl[0].get("printed_page") if bl else None
        if pn and str(pn).isdecimal() and float(bl[0].get("printed_page_conf") or 0) >= 0.6:
            cands.append({"arabic": {int(pn)}, "roman": set()})
            continue
        lines = [l for b in bl for l in b["text"].splitlines() if l.strip()]
        cands.append(page_number_candidates(lines[:2] + lines[-2:]) if lines else {"arabic": set(), "roman": set()})
    res = resolve_printed_pages(cands)
    for pg, bl in by_page.items():
        if not bl[0].get("printed_page") and 1 <= pg <= n_pages:
            pn, cf = res[pg - 1]
            if pn:
                for b in bl:
                    b["printed_page"], b["printed_page_conf"] = pn, cf
    return blocks


def merge_ocr(extract_dir: Path, meta: dict, blocks: list[dict]) -> list[dict]:
    """Ganti halaman yang sudah di-OCR dengan teks OCR (lapis 1 PyMuPDF tetap utuh di disk)."""
    ocr_dir = extract_dir / "ocr" / meta["sha256"]
    if not ocr_dir.exists():
        return blocks
    ocr = {}
    for f in ocr_dir.glob("*.json"):
        r = json.loads(f.read_text(encoding="utf-8"))
        if r.get("text", "").strip():
            ocr[int(r["pdf_page"])] = r
    if not ocr:
        return blocks
    by_page: dict[int, list[dict]] = {}
    for b in blocks:
        by_page.setdefault(b["pdf_page"], []).append(b)
    # info halaman (nomor cetak dll.) dari baris kualitas, termasuk halaman tanpa blok
    rep = {}
    for x in csv.DictReader(open(extract_dir / "_quality_report.csv", encoding="utf-8")):
        if x["sha"] == meta["sha256"][:12]:
            rep[int(x["pdf_page"])] = x
    for pg, r in ocr.items():
        q = rep.get(pg, {})
        base = (by_page.get(pg) or [{}])[0]
        paras = [p.strip() for p in re.split(r"\n\s*\n", r["text"]) if p.strip()]
        by_page[pg] = [{
            "doc": meta["sha256"], "pdf_page": pg,
            "printed_page": base.get("printed_page", q.get("printed_page") or None),
            "printed_page_conf": float(base.get("printed_page_conf", q.get("printed_conf") or 0) or 0),
            "page_type": base.get("page_type", q.get("page_type", "prose")) if base.get("page_type") != "figure" else "prose",
            "block": i, "type": "heading" if _ocr_heading(t) else "paragraph", "text": t, "bbox": None, "engine": r["engine"],
            "engine_version": r["engine_version"], "extract_version": "ocr",
            "page_hash": hashlib.sha1(r["text"].encode("utf-8")).hexdigest()[:16],
            "needs_ocr": (["ocr_loop_trimmed"] if r.get("loop_trimmed") else ["ocr_truncated"] if r.get("truncated") else []),
            "lang": "en"} for i, t in enumerate(paras)]
    return [b for pg in sorted(by_page) for b in by_page[pg]]


def compact(root: Path) -> None:
    """Hapus versi lama tabel LanceDB (setiap build menumpuk versi baru)."""
    from datetime import timedelta
    db = lancedb.connect(str(root / ".rag" / "index" / "lancedb"))
    for name in ("pages", "chunks", "docs"):
        try:
            db.open_table(name).optimize(cleanup_older_than=timedelta(seconds=0), delete_unverified=True)
        except Exception as e:  # noqa: BLE001
            console.print(f"[yellow]compact {name}: {e}[/]")


def build(folder: str, dry_run: bool = False):
    root = Path(folder).resolve()
    ext = root / ".rag" / "extract"
    idx_dir = root / ".rag" / "index" / "lancedb"
    idx_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    pages_rows, chunk_rows, doc_rows = [], [], []
    n_docs = 0
    for meta, blocks in iter_docs(ext):
        n_docs += 1
        sha = meta["sha256"]
        title = meta.get("title") or meta["file"]          # tetap dipakai di header embedding (cache tidak berubah)
        year = meta.get("year_guess")
        try:
            _oa = json.loads((ext / "openalex" / f"{sha}.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _oa = None
        shown, title_src = display_title(meta, _oa)
        # --- halaman ---
        by_page: dict[int, list[dict]] = {}
        for b in blocks:
            by_page.setdefault(b["pdf_page"], []).append(b)
        for pno, bl in sorted(by_page.items()):
            b0 = bl[0]
            pages_rows.append(dict(
                doc=sha, file=meta["rel_path"], pdf_page=pno, printed_page=b0["printed_page"] or "",
                printed_conf=float(b0["printed_page_conf"]), page_type=b0["page_type"],
                needs_ocr=";".join(b0["needs_ocr"]), engine=b0["engine"], page_hash=b0["page_hash"],
                text="\n".join(x["text"] for x in bl)))
        # --- chunk berdasarkan struktur ---
        section = ""
        buf: list[dict] = []

        def flush(buf: list[dict], section: str) -> None:
            if not buf:
                return
            text = "\n".join(x["text"] for x in buf)
            p0, p1 = buf[0], buf[-1]
            types = {x["page_type"] for x in buf}
            low = any(x["needs_ocr"] for x in buf)
            chunk_rows.append(dict(
                chunk_id=f"{sha[:12]}:{p0['pdf_page']}:{p0['block']}", doc=sha, file=meta["rel_path"],
                title=shown, year=year or 0, section=section[:200],
                pdf_page_start=p0["pdf_page"], pdf_page_end=p1["pdf_page"],
                printed_page_start=p0["printed_page"] or "", printed_page_end=p1["printed_page"] or "",
                printed_conf=float(min(x["printed_page_conf"] for x in buf)),
                kind="references" if types == {"references"} else "body",
                quality_flag="ocr_pending" if low else "",
                header=f"{title} ({year or 'n.d.'}) > {section[:120]}".strip(" >"),
                text=text))

        words = 0
        for b in blocks:
            if b["page_type"] in ("blank",) or b.get("boiler"):
                continue
            if b["type"] == "heading" and len(b["text"]) < 150:
                if words >= TARGET_WORDS // 3:
                    flush(buf, section)
                    buf, words = [], 0
                section = b["text"].replace("\n", " ")
            buf.append(b)
            words += len(b["text"].split())
            if words >= TARGET_WORDS:
                flush(buf, section)
                buf = buf[-OVERLAP_BLOCKS:] if OVERLAP_BLOCKS and len(buf[-1]["text"].split()) < MAX_WORDS // 3 else []
                words = sum(len(x["text"].split()) for x in buf)
        flush(buf, section)
        doc_rows.append(dict(
            doc=sha, file=meta["rel_path"], title=shown, title_source=title_src, year=year or 0, type=meta.get("type_guess", ""),
            pages=meta["pages"], doi=meta.get("doi") or "", frac_need_ocr=float(meta["frac_need_ocr"]),
            printed_page_coverage=float(meta["printed_page_coverage"]),
            page_types=json.dumps(meta.get("page_types", {})), **_enrich.for_doc(root, sha)))
        console.print(f"[dim]{n_docs:>3}[/] {meta['rel_path'][:70]}")

    if dry_run:
        return chunk_rows, pages_rows
    db = lancedb.connect(str(idx_dir))
    for name, rows in (("pages", pages_rows), ("chunks", chunk_rows), ("docs", doc_rows)):
        db.create_table(name, data=pa.Table.from_pylist(rows), mode="overwrite")
    db.open_table("chunks").create_fts_index("text", replace=True, stem=True, remove_stop_words=True)
    db.open_table("pages").create_fts_index("text", replace=True, stem=True, remove_stop_words=True)

    manifest = dict(
        library=root.name, path=str(root), built_at=datetime.now().isoformat(timespec="seconds"),
        extract_version=PARSER_VERSION, chunker=CHUNKER_VERSION,
        chunk_params=dict(target_words=TARGET_WORDS, max_words=MAX_WORDS, overlap_blocks=OVERLAP_BLOCKS),
        lexical="lancedb-fts (BM25, stem, stopwords)", dense=None, reranker=None,
        n_docs=n_docs, n_pages=len(pages_rows), n_chunks=len(chunk_rows))
    (root / ".rag" / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    register(root.name, root)
    _settings.ask(root)
    if (root / ".rag" / "index" / "emb").exists():     # tempel vector embedding yang sudah ada di cache
        from .embed import attach
        console.print(f"vector embedding ditempel: {attach(root)} chunk")
    compact(root)
    console.print(f"[bold green]SELESAI[/] {n_docs} dokumen, {len(pages_rows)} halaman, {len(chunk_rows)} chunk "
                  f"dalam {time.time()-t0:.0f} dtk. Terdaftar sebagai pustaka '{root.name}'.")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "build":
        print(__doc__); sys.exit(1)
    build(sys.argv[2])
