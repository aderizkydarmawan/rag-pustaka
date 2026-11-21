"""Server MCP "rag-pustaka" — dipanggil Claude Code dari folder mana pun.

Pencarian hibrida (BM25 + embedding Qwen3, digabung RRF) + akses bertingkat + alat anti-bias.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import lancedb

from . import settings as _settings
from .library import find_library, registry

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore

MAX_WORDS_PER_HIT = 300
MAX_WORDS_PER_CALL = 4000
DOMINANCE_WARN = 0.5      # nilai awal (SPEC §8.2)

mcp = MCPServer("rag-pustaka", instructions=(
    "Pencarian pustaka riset lokal milik pengguna. Hasil hanya dari koleksi pengguna, bukan seluruh literatur. "
    "Ikuti CLAUDE.md: baca konteks (get_context/read_section) sebelum memakai potongan, sebutkan file + halaman, "
    "jalankan pencarian bukti berlawanan (prosedur: search dengan negasi, search_exact 'no significant|did not|limitation', "
    "list_documents), perhatikan source_spread dan coverage, dan cek kutipan dengan verify_citation sebelum jawaban akhir."))


# ---------- utilitas ----------
def _words(s: str, n: int) -> str:
    w = s.split()
    return s if len(w) <= n else " ".join(w[:n]) + " …"


def _resolve(library: str | None) -> Path:
    reg = registry()
    if library:
        if library in reg:
            return Path(reg[library])
        p = Path(library)
        if (p / ".rag").exists():
            return p.resolve()
        hits = [v for k, v in reg.items() if library.lower() in k.lower()]
        if len(hits) == 1:
            return Path(hits[0])
        raise ValueError(f"Pustaka '{library}' tidak ditemukan. Terdaftar: {list(reg)}")
    found = find_library(os.environ.get("RAG_LIBRARY") or os.getcwd())
    if found:
        return found
    if len(reg) == 1:
        return Path(next(iter(reg.values())))
    raise ValueError(f"Sebutkan parameter library. Terdaftar: {list(reg)}")


@lru_cache(maxsize=8)
def _db(path: str):
    return lancedb.connect(str(Path(path) / ".rag" / "index" / "lancedb"))


def _stamp(path: str) -> float:
    """Penanda versi indeks: berubah setiap build/embed menulis manifest, sehingga cache di bawah ikut segar."""
    try:
        return (Path(path) / ".rag" / "manifest.json").stat().st_mtime
    except OSError:
        return 0.0


@lru_cache(maxsize=8)
def _docs_c(path: str, stamp: float):
    return _db(path).open_table("docs").to_pandas()


def _docs(path: str):
    return _docs_c(path, _stamp(path))


@lru_cache(maxsize=4)
def _pages_c(path: str, stamp: float):
    return _db(path).open_table("pages").to_pandas()


def _pages(path: str):
    return _pages_c(path, _stamp(path))


def _doc_row(root: Path, doc: str):
    d = _docs(str(root))
    m = d[d["doc"].str.startswith(doc)] if re.fullmatch(r"[0-9a-f]{6,64}", doc) else d[d["file"].str.contains(re.escape(doc), case=False)]
    if len(m) == 0:
        raise ValueError(f"Dokumen '{doc}' tidak ditemukan.")
    if len(m) > 1:
        raise ValueError(f"'{doc}' cocok dengan {len(m)} dokumen: {m['file'].head(8).tolist()}. Perjelas.")
    return m.iloc[0]


def _is_pending(v: str) -> bool:
    """Halaman masih menunggu OCR: ada alasan dari tahap ekstraksi yang belum diganti hasil OCR.
    Tanda 'ocr_*' (ocr_truncated, ocr_loop_trimmed) adalah tanda mutu hasil OCR, bukan antrian."""
    return any(x and not x.startswith("ocr_") for x in (v or "").split(";"))


def _page_stats(path: str) -> dict:
    return _page_stats_c(path, _stamp(path))


@lru_cache(maxsize=8)
def _page_stats_c(path: str, stamp: float) -> dict:
    """Keadaan halaman dihitung dari indeks yang sedang dipakai (bukan dari catatan tahap ekstraksi,
    yang tidak berubah setelah OCR selesai)."""
    d = _docs(path)
    p = _db(path).open_table("pages").search().select(["doc", "needs_ocr"]).limit(10_000_000).to_pandas()
    have = p.groupby("doc").size()
    pend = p[p["needs_ocr"].map(_is_pending)].groupby("doc").size()
    per = {}
    for _, r in d.iterrows():
        n = int(r["pages"])
        no_text = max(n - int(have.get(r["doc"], 0)), 0)
        q = int(pend.get(r["doc"], 0))
        per[r["doc"]] = {"no_text": no_text, "pending": q, "frac": round((no_text + q) / n, 3) if n else 0.0}
    return {"per_doc": per, "flagged": int(p["needs_ocr"].fillna("").str.startswith("ocr_").sum())}


def _coverage(root: Path) -> dict:
    d = _docs(str(root))
    st = _page_stats(str(root))
    per = st["per_doc"]
    pending = sum(v["pending"] for v in per.values())
    no_text = sum(v["no_text"] for v in per.values())
    gaps = sorted(((per[r["doc"]]["frac"], r["file"]) for _, r in d.iterrows() if per[r["doc"]]["frac"] >= 0.2), reverse=True)
    note = ("Semua halaman yang ditandai perlu OCR sudah di-OCR." if pending == 0
            else f"{pending} halaman ditandai mutu teks digitalnya rendah dan masuk antrian OCR; teks digital itu tetap ikut dicari, tapi bisa berisi salah baca.")
    if no_text:
        note += f" {no_text} halaman tidak punya teks di indeks (kosong atau hanya gambar) dan tidak bisa ditemukan."
    return {"docs_total": int(len(d)), "pages_queued_for_ocr": int(pending), "pages_without_text": int(no_text),
            "ocr_pages_flagged_quality": st["flagged"],
            "docs_with_20pct_or_more_pages_unsearchable": [{"file": f, "unsearchable_frac": fr} for fr, f in gaps[:6]],
            "note": note}


def _spread(hits: list[dict], root: Path) -> dict:
    c = Counter(h["file"] for h in hits)
    n = len(hits) or 1
    top_file, top_n = (c.most_common(1)[0] if c else ("", 0))
    years = [h["year"] for h in hits if h.get("year")]
    hhi = round(sum((v / n) ** 2 for v in c.values()), 3)
    out = {"unique_documents": len(c), "hits_per_document": dict(c.most_common()),
           "year_range": [min(years), max(years)] if years else None, "concentration_hhi": hhi,
           "collection_documents": int(len(_docs(str(root))))}
    if top_n / n > DOMINANCE_WARN:
        out["warning"] = (f"{top_n}/{len(hits)} hasil dari satu dokumen ({top_file}). Sebutkan sebagai keterbatasan "
                          f"dan cari dari sumber lain.")
    return out


def _log(root: Path, name: str, rec: dict) -> None:
    d = root / ".rag" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    rec["ts"] = datetime.now().isoformat(timespec="seconds")
    with open(d / name, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


PRIVATE_NOTE = ("Folder ini PRIVATE (atau belum diputuskan): teks tidak dikirim, hanya metadata. "
                "Baca langsung di PDF-nya. Ubah: python -m rag.settings <folder>")


def _priv(root: Path) -> bool:
    return _settings.is_private(root)


def _privacy(root: Path) -> dict:
    s = _settings.load(root)
    return {"private": _priv(root), "lookup": (s or {}).get("lookup", "off"), "decided": s is not None}


def _cite(r: dict) -> dict:
    pp = r["printed_page_start"]
    use_printed = bool(pp) and r["printed_conf"] >= 0.8
    return {"printed_page": pp or None, "pdf_page": int(r["pdf_page_start"]),
            "pdf_page_end": int(r["pdf_page_end"]), "page_cite_basis": "printed" if use_printed else "pdf"}


# ---------- alat ----------
@mcp.tool()
def list_libraries() -> dict:
    """Daftar pustaka (folder ber-indeks) yang terdaftar, plus pustaka yang dipakai bawaan dari folder kerja sekarang."""
    reg = registry()
    try:
        default = str(_resolve(None))
    except ValueError:
        default = None
    out = {}
    for k, v in reg.items():
        try:
            m = json.loads((Path(v) / ".rag" / "manifest.json").read_text(encoding="utf-8"))
            out[k] = {"path": v, "docs": m["n_docs"], "chunks": m["n_chunks"], "dense": m.get("dense"), "built_at": m["built_at"],
                      **_privacy(Path(v))}
        except Exception as e:  # noqa: BLE001
            out[k] = {"path": v, "error": str(e)}
    return {"libraries": out, "default_from_cwd": default}


COLS = ["chunk_id", "doc", "file", "title", "year", "section", "pdf_page_start", "pdf_page_end",
        "printed_page_start", "printed_page_end", "printed_conf", "kind", "quality_flag", "text"]
RRF_K = 60
RERANK_N = 30
_embedder: dict = {}


def _embed_query(query: str):
    """Vector query (Qwen3-Embedding-4B, dimuat sekali per proses server; ±30 dtk pertama kali)."""
    from .embed import encode, load_model, query_text
    if "m" not in _embedder:
        _embedder["m"] = load_model()
    tok, model = _embedder["m"]
    return encode(tok, model, [query_text(query)])[0].astype("float32")


def _dense_ok(root: Path) -> bool:
    try:
        m = json.loads((root / ".rag" / "manifest.json").read_text(encoding="utf-8"))
        # makna hanya dipakai bila hampir semua chunk punya vector (kalau tidak, hasil berat sebelah)
        return bool(m.get("dense")) and m["dense"].get("n_with_vector", 0) >= 0.98 * m.get("n_chunks", 1)
    except Exception:  # noqa: BLE001
        return False


@mcp.tool()
def search(query: str, library: str | None = None, k: int = 10, include_references: bool = False,
           year_from: int | None = None, year_to: int | None = None, mode: str = "hybrid",
           rerank: bool = True) -> dict:
    """Cari potongan teks relevan di pustaka. mode: "hybrid" (bawaan: kata kunci BM25 + makna/embedding,
    digabung RRF), "bm25" (kata persis), atau "dense" (makna saja).
    rerank=True (bawaan): 30 kandidat teratas diurutkan ulang oleh Qwen3-Reranker (skor 0–1 = relevansi menurut model).
    Tulis query dalam bahasa Inggris dengan istilah teknis. Hasil = kemiripan, BUKAN bobot bukti.
    Selalu baca source_spread dan coverage."""
    root = _resolve(library)
    k = max(1, min(k, 30))
    n_cand = k * 4
    t = _db(str(root)).open_table("chunks")
    lists, rows_by_id, notes = {}, {}, []
    if mode in ("hybrid", "bm25"):
        r = t.search(query, query_type="fts").select(COLS).limit(n_cand).to_list()
        lists["bm25"] = r
    if mode in ("hybrid", "dense"):
        if _dense_ok(root):
            try:
                v = _embed_query(query)
                r = (t.search(v, vector_column_name="vector").metric("cosine").where("has_vector = true", prefilter=True)
                     .select(COLS).limit(n_cand).to_list())
                lists["dense"] = r
            except Exception as e:  # noqa: BLE001
                notes.append(f"pencarian makna gagal ({type(e).__name__}: {e}); memakai BM25 saja")
        else:
            notes.append("indeks makna belum lengkap (embedding sedang/belum dijalankan); memakai BM25 saja")
        if "dense" not in lists and "bm25" not in lists:
            lists["bm25"] = t.search(query, query_type="fts").select(COLS).limit(n_cand).to_list()
    fused, ranks = Counter(), {}
    for name, rs in lists.items():
        for i, r in enumerate(rs, 1):
            cid = r["chunk_id"]
            rows_by_id.setdefault(cid, r)
            fused[cid] += 1.0 / (RRF_K + i)
            ranks.setdefault(cid, {})[name] = {"rank": i, "score": round(float(r.get("_score", r.get("_distance", 0)) or 0), 3)}
    cand = []
    for cid, sc in fused.most_common():
        r = rows_by_id[cid]
        if not include_references and r["kind"] == "references":
            continue
        if year_from and r["year"] and r["year"] < year_from:
            continue
        if year_to and r["year"] and r["year"] > year_to:
            continue
        cand.append((cid, sc))
    rr = {}
    if rerank and cand:
        top = cand[:RERANK_N]
        try:
            from .rerank import score
            ss = score(query, [rows_by_id[c]["title"] + "\n" + rows_by_id[c]["text"] for c, _ in top])
            rr = {c: s_ for (c, _), s_ in zip(top, ss)}
            cand = sorted(top, key=lambda x: -rr[x[0]]) + cand[RERANK_N:]
        except Exception as e:  # noqa: BLE001
            notes.append(f"reranker gagal ({type(e).__name__}: {e}); urutan RRF dipakai")
    hits, words = [], 0
    for cid, sc in cand:
        r = rows_by_id[cid]
        txt = _words(r["text"], MAX_WORDS_PER_HIT)
        words += len(txt.split())
        if words > MAX_WORDS_PER_CALL:
            break
        scores = {"rrf": round(sc, 4), **ranks[cid]}
        if cid in rr:
            scores["rerank"] = round(rr[cid], 3)
        hits.append({"rank": len(hits) + 1, "chunk_id": cid, "file": r["file"], "title": r["title"],
                     "year": r["year"] or None, "section": r["section"], **_cite(r),
                     "scores": scores, "found_by": sorted(ranks[cid]),
                     "quality_flag": r["quality_flag"] or None, "text": txt})
        if len(hits) >= k:
            break
    qid = uuid.uuid4().hex[:10]
    used = "+".join(sorted(lists))
    _log(root, "queries.jsonl", {"query_id": qid, "tool": "search", "query": query, "k": k, "mode": used,
                                  "returned": [h["chunk_id"] for h in hits]})
    out = {"query_id": qid, "library": root.name, "scope_note": f"Hanya dari koleksi '{root.name}', bukan seluruh literatur.",
           "results": hits, "source_spread": _spread(hits, root), "coverage": _coverage(root),
           "params_used": {"mode": used, "fusion": "RRF k=60" if len(lists) > 1 else None, "reranker": "Qwen3-Reranker-4B" if rr else None,
                           "k": k, "include_references": include_references}}
    if notes:
        out["notes"] = notes
    out["privacy"] = _privacy(root)
    if _priv(root):
        for h in out["results"]:
            h["text"] = None
        out["privacy"]["note"] = PRIVATE_NOTE
    return out


@mcp.tool()
def search_exact(phrase: str, library: str | None = None, regex: bool = False, k: int = 30,
                 doc: str | None = None) -> dict:
    """Cari kecocokan PERSIS (istilah, singkatan, angka, frasa seperti 'no significant', 'did not').
    Melaporkan berapa halaman belum bisa dicari karena menunggu OCR."""
    root = _resolve(library)
    p = _pages(str(root))
    if doc:
        p = p[p["doc"] == _doc_row(root, doc)["doc"]]
    pat = phrase if regex else re.escape(phrase)
    m = p[p["text"].str.contains(pat, case=False, regex=True, na=False)]
    out = []
    for _, r in m.head(k).iterrows():
        mt = re.search(pat, r["text"], flags=re.I)
        s = max(0, mt.start() - 200) if mt else 0
        out.append({"file": r["file"], "pdf_page": int(r["pdf_page"]), "printed_page": r["printed_page"] or None,
                    "context": None if _priv(root) else r["text"][s:s + 500].replace("\n", " ")})
    qid = uuid.uuid4().hex[:10]
    _log(root, "queries.jsonl", {"query_id": qid, "tool": "search_exact", "query": phrase, "n": int(len(m))})
    return {"query_id": qid, "total_matches_pages": int(len(m)), "shown": len(out), "results": out,
            "documents_matched": int(m["doc"].nunique()),
            "pages_queued_for_ocr": int(p["needs_ocr"].map(_is_pending).sum()), "coverage": _coverage(root)}


@mcp.tool()
def list_documents(library: str | None = None, title_contains: str | None = None, year_from: int | None = None,
                   year_to: int | None = None, doc_type: str | None = None, limit: int = 300) -> dict:
    """Kartu sederhana semua dokumen di pustaka (judul, tahun, jenis, halaman, kualitas OCR).
    Pakai untuk melihat SELURUH koleksi, bukan hanya yang muncul di pencarian."""
    root = _resolve(library)
    d = _docs(str(root))
    if title_contains:
        d = d[d["title"].str.contains(title_contains, case=False) | d["file"].str.contains(title_contains, case=False)]
    if year_from:
        d = d[(d["year"] == 0) | (d["year"] >= year_from)]
    if year_to:
        d = d[(d["year"] == 0) | (d["year"] <= year_to)]
    if doc_type:
        d = d[d["type"] == doc_type]
    per = _page_stats(str(root))["per_doc"]
    rows = [{"doc": r["doc"][:12], "file": r["file"], "year": int(r["year"]) or None, "type": r["type"],
             "pages": int(r["pages"]), "unsearchable_frac": round(per[r["doc"]]["frac"], 2),
             "doi": r["doi"] or None, "authors": r.get("authors") or None,
             "retracted": r.get("retracted", "belum dicek")} for _, r in d.head(limit).iterrows()]
    return {"library": root.name, "count": int(len(d)), "documents": rows,
            "card_note": ("Kartu sederhana (otomatis). Tahun dari nama file; penulis + status retraction dari OpenAlex "
                          "hanya untuk dokumen ber-DOI. retracted='belum dicek' BUKAN berarti tidak ditarik."),
            "privacy": _privacy(root)}


@mcp.tool()
def get_card(doc: str, library: str | None = None) -> dict:
    """Kartu dokumen (sederhana). Kartu adalah penunjuk, bukan bukti: klaim tetap dikutip dari halaman asli."""
    root = _resolve(library)
    r = _doc_row(root, doc)
    return {"doc": r["doc"], "file": r["file"], "title": r["title"], "year": int(r["year"]) or None, "type": r["type"],
            "pages": int(r["pages"]), "doi": r["doi"] or None, "page_types": json.loads(r["page_types"]),
            "unsearchable_frac": _page_stats(str(root))["per_doc"][r["doc"]]["frac"],
            "scanned_frac_at_extract": float(r["frac_need_ocr"]), "printed_page_coverage": float(r["printed_page_coverage"]),
            "title_source": r.get("title_source"), "retracted": r["retracted"], "authors": r.get("authors") or None,
            "openalex_year": int(r.get("oa_year") or 0) or None, "meta_source": r.get("meta_source", "nama file"),
            "retracted_note": "'belum dicek' BUKAN berarti tidak ditarik; tetap pakai citation-verification sebelum naskah.",
            "card_level": "simple", "confidence": "low"}


@mcp.tool()
def get_page(doc: str, pdf_page: int, library: str | None = None, image: bool = False) -> dict:
    """Teks satu halaman penuh. image=True juga merender gambar halaman (PNG) supaya teks/rumus bisa dicek."""
    root = _resolve(library)
    r = _doc_row(root, doc)
    p = _pages(str(root))
    row = p[(p["doc"] == r["doc"]) & (p["pdf_page"] == pdf_page)]
    out = {"file": r["file"], "pdf_page": pdf_page}
    if len(row):
        x = row.iloc[0]
        out.update(printed_page=x["printed_page"] or None, page_type=x["page_type"], engine=x["engine"],
                   needs_ocr=x["needs_ocr"] or None, page_hash=x["page_hash"],
                   text=None if _priv(root) else _words(x["text"], MAX_WORDS_PER_CALL))
        if _priv(root):
            out["note"] = PRIVATE_NOTE
    else:
        out.update(text=None, note="Halaman tanpa teks (kemungkinan scan, menunggu OCR). Pakai image=True.")
    if image:
        import pymupdf
        cache = Path.home() / ".rag" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        png = cache / f"{r['doc'][:12]}_p{pdf_page}.png"
        if not png.exists():
            pymupdf.open(root / r["file"])[pdf_page - 1].get_pixmap(dpi=110).save(png)
        out["image_path"] = str(png)
    return out


@mcp.tool()
def get_context(chunk_id: str, library: str | None = None, window: int = 1) -> dict:
    """Teks di sekitar sebuah chunk: halaman chunk itu ± `window` halaman. Wajib dibaca sebelum chunk dipakai."""
    root = _resolve(library)
    sha12, pg, _ = chunk_id.split(":")
    d = _docs(str(root))
    r = d[d["doc"].str.startswith(sha12)].iloc[0]
    p = _pages(str(root))
    rows = p[(p["doc"] == r["doc"]) & p["pdf_page"].between(int(pg) - window, int(pg) + window + 1)].sort_values("pdf_page")
    text = "\n\n".join(f"[pdf {int(x.pdf_page)} | cetak {x.printed_page or '?'}]\n{x.text}" for x in rows.itertuples())
    if _priv(root):
        return {"file": r["file"], "pages": rows["pdf_page"].astype(int).tolist(), "text": None, "note": PRIVATE_NOTE}
    return {"file": r["file"], "pages": rows["pdf_page"].astype(int).tolist(), "text": _words(text, MAX_WORDS_PER_CALL)}


@mcp.tool()
def get_outline(doc: str, library: str | None = None) -> dict:
    """Daftar isi perkiraan (judul bagian + halaman) dari deteksi heading. Jujur bila tidak tersedia."""
    root = _resolve(library)
    r = _doc_row(root, doc)
    c = _db(str(root)).open_table("chunks").search().where(f"doc = '{r['doc']}'").limit(100000).to_pandas()
    c = c.sort_values("pdf_page_start")
    seen, out = set(), []
    for x in c.itertuples():
        s = (x.section or "").strip()
        if s and s not in seen and len(s) < 120:
            seen.add(s)
            out.append({"section": s, "pdf_page": int(x.pdf_page_start), "printed_page": x.printed_page_start or None})
    ok = len(out) >= 3
    return {"file": r["file"], "pages": int(r["pages"]), "outline_available": ok,
            "outline": out[:400] if ok else [],
            "note": None if ok else "Outline tidak terdeteksi (umum pada buku scan). Gunakan read_section dengan rentang halaman.",
            "quality": "heuristik; bisa memuat baris yang bukan judul"}


@mcp.tool()
def read_section(doc: str, pdf_page_start: int, pdf_page_end: int, library: str | None = None) -> dict:
    """Baca rentang halaman utuh (maks ±4000 kata). Pakai untuk membaca satu bagian/subbab lengkap."""
    root = _resolve(library)
    r = _doc_row(root, doc)
    p = _pages(str(root))
    rows = p[(p["doc"] == r["doc"]) & p["pdf_page"].between(pdf_page_start, pdf_page_end)].sort_values("pdf_page")
    text = "\n\n".join(f"[pdf {int(x.pdf_page)} | cetak {x.printed_page or '?'}]\n{x.text}" for x in rows.itertuples())
    missing = sorted(set(range(pdf_page_start, pdf_page_end + 1)) - set(rows["pdf_page"].astype(int)))
    if _priv(root):
        return {"file": r["file"], "text": None, "note": PRIVATE_NOTE, "pages_without_text": missing}
    return {"file": r["file"], "text": _words(text, MAX_WORDS_PER_CALL), "pages_without_text": missing,
            "truncated": len(text.split()) > MAX_WORDS_PER_CALL}


@mcp.tool()
def verify_citation(doc: str, pdf_page: int, quote: str, library: str | None = None) -> dict:
    """Cek apakah kutipan benar ada di halaman tersebut (toleran spasi/tanda hubung). Wajib sebelum jawaban akhir."""
    root = _resolve(library)
    r = _doc_row(root, doc)
    p = _pages(str(root))
    rows = p[(p["doc"] == r["doc"]) & p["pdf_page"].between(pdf_page - 1, pdf_page + 1)]
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    qn = norm(quote)
    best = (0.0, None)
    for x in rows.itertuples():
        tn = norm(x.text)
        if qn and qn in tn:
            best = (1.0, int(x.pdf_page)); break
        sm = difflib.SequenceMatcher(None, tn, qn, autojunk=False)
        m = sm.find_longest_match(0, len(tn), 0, len(qn))
        ratio = m.size / max(1, len(qn))
        if ratio > best[0]:
            best = (ratio, int(x.pdf_page))
    verdict = "found" if best[0] >= 0.98 else "partial" if best[0] >= 0.6 else "not_found"
    _log(root, "citations.jsonl", {"doc": r["file"], "pdf_page": pdf_page, "verdict": verdict, "score": round(best[0], 2)})
    return {"verdict": verdict, "match_score": round(best[0], 2), "found_on_pdf_page": best[1],
            "note": "not_found bisa berarti halaman belum di-OCR atau kutipan diparafrasekan — jangan klaim sebagai kutipan langsung."}


@mcp.tool()
def log_usage(query_id: str, used: list[str], discarded: list[str], reason: str = "", library: str | None = None) -> dict:
    """Catat chunk yang DIPAKAI dan DIBUANG dari sebuah pencarian (audit bias pembaca)."""
    root = _resolve(library)
    _log(root, "usage.jsonl", {"query_id": query_id, "used": used, "discarded": discarded, "reason": reason})
    return {"ok": True}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
