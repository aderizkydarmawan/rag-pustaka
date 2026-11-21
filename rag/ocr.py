"""Tahap 2: OCR halaman yang ditandai lapis 1 dengan PaddleOCR-VL-1.6 (GPU).

- Urutan: dokumen dengan halaman OCR paling sedikit dulu (cakupan dokumen naik cepat).
- Hasil per halaman: .rag/extract/ocr/<sha>/<pdf_page>.json (lapis 1 tetap utuh).
- Bisa dihentikan kapan saja (Ctrl+C atau buat file .rag/STOP); dilanjutkan otomatis.
- Halaman 'rumus rusak tapi prosa bagus' dilewati (prioritas kedua, --formula untuk menyertakan).

Pemakaian:  python -m rag.ocr "<folder>" [--batch 4] [--max-pages N] [--formula]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pymupdf
import torch
from PIL import Image
from rich.console import Console

from .loopdetect import trim
from rich.progress import (BarColumn, MofNCompleteColumn, Progress, TextColumn,
                           TimeElapsedColumn, TimeRemainingColumn)

from . import MODELS_DIR
MODEL = str(MODELS_DIR / "PaddlePaddle__PaddleOCR-VL-1.6")
ENGINE, ENGINE_VERSION = "paddleocr-vl", "1.6-hf-transformers"
MAX_PIXELS = 700 * 28 * 28        # lebih besar → VRAM meluber & sangat lambat (uji 2026-11-13)
MAX_NEW_TOKENS = 2048             # 1536 terlalu pendek untuk halaman indeks/tabel padat (45 hlm terpotong, 2026-11-13)
DPI = 150
console = Console()


def todo_pages(root: Path, include_formula: bool) -> list[tuple[str, str, int]]:
    rep = root / ".rag" / "extract" / "_quality_report.csv"
    queue = json.loads((root / ".rag" / "extract" / "_queue.json").read_text(encoding="utf-8"))
    sha_full = {k[:12]: k for k in queue}
    u = {}
    for x in csv.DictReader(open(rep, encoding="utf-8")):
        u[(x["sha"], x["pdf_page"])] = x
    per_doc = defaultdict(list)
    for (sha12, pg), x in u.items():
        why = x["needs_ocr"]
        if not why:
            continue
        formula_only = why == "karakter_rusak" and x["dict_ratio"] and float(x["dict_ratio"]) >= 0.9
        if formula_only and not include_formula:
            continue
        full = sha_full.get(sha12)
        if full:
            per_doc[full].append((queue[full]["file"], int(pg)))
    out = []
    for sha in sorted(per_doc, key=lambda s: len(per_doc[s])):
        for f, pg in sorted(per_doc[sha], key=lambda t: t[1]):
            out.append((sha, f, pg))
    return out


REDO_MAX_TOKENS = 4096


def redo_quality(rec: dict) -> tuple[bool, int]:
    """Nilai mutu satu hasil OCR untuk memilih antara hasil lama dan hasil --redo.
    1) hasil bersih (tidak terpotong, tidak macet) selalu menang atas hasil bermasalah;
    2) di antara yang sama-sama bersih/bermasalah: menang yang punya lebih banyak kata BERBEDA
       setelah sisa pengulangan dipotong. Dulu dipakai panjang teks mentah, sehingga hasil yang
       macet berulang (panjang karena sampah) bisa menang (bug ditemukan 2026-11-17)."""
    clean = not rec.get("truncated") and not rec.get("loop_trimmed")
    text, _ = trim(rec.get("text", ""))
    return clean, len(set(text.split()))


def redo_pages(root: Path) -> list[tuple[str, str, int]]:
    """Halaman OCR yang terpotong, macet berulang, atau hampir kosong padahal PyMuPDF melihat banyak teks."""
    chars = {(x["sha"], int(x["pdf_page"])): int(x["chars"] or 0) for x in
             csv.DictReader(open(root / ".rag" / "extract" / "_quality_report.csv", encoding="utf-8"))}
    out = []
    for f in sorted((root / ".rag" / "extract" / "ocr").glob("*/*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        if r.get("redo"):
            continue
        short = len(r["text"].strip()) < 50 and chars.get((r["doc"][:12], r["pdf_page"]), 0) > 300
        if r.get("truncated") or r.get("loop_trimmed") or short:
            out.append((r["doc"], r["file"], r["pdf_page"]))
    return out


def render(pdf: Path, pg: int) -> Image.Image:
    pix = pymupdf.open(pdf)[pg - 1].get_pixmap(dpi=DPI)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    s = (MAX_PIXELS / (img.width * img.height)) ** 0.5
    if s < 1:
        img = img.resize((max(28, int(img.width * s) // 28 * 28), max(28, int(img.height * s) // 28 * 28)), Image.LANCZOS)
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--max-pages", type=int, default=0)
    ap.add_argument("--max-docs", type=int, default=0, help="proses hanya N dokumen berikutnya (mis. 1 = satu buku)")
    ap.add_argument("--formula", action="store_true")
    ap.add_argument("--redo", action="store_true", help="OCR ulang halaman terpotong/macet/terlalu pendek (sekali saja)")
    a = ap.parse_args()
    root = Path(a.folder).resolve()
    out_root = root / ".rag" / "extract" / "ocr"
    stop_file = root / ".rag" / "STOP"
    log = open(root / ".rag" / "logs" / "ocr.log", "a", encoding="utf-8")

    def say(msg, style=""):
        s = datetime.now().strftime("%H:%M:%S")
        log.write(f"{s} {msg}\n"); log.flush()
        console.print(f"[dim]{s}[/] {msg}", style=style)

    max_tok, rep_pen = MAX_NEW_TOKENS, None
    if a.redo:
        todo = redo_pages(root)
        max_tok, rep_pen = REDO_MAX_TOKENS, 1.05
    else:
        todo = [t for t in todo_pages(root, a.formula) if not (out_root / t[0] / f"{t[2]}.json").exists()]
    if a.max_docs:
        keep = list(dict.fromkeys(t[0] for t in todo))[: a.max_docs]
        todo = [t for t in todo if t[0] in keep]
    if a.max_pages:
        todo = todo[: a.max_pages]
    n_docs = len({t[0] for t in todo})
    if a.max_docs:
        say("Dokumen sesi ini: " + ", ".join(dict.fromkeys(t[1] for t in todo)), "bold cyan")
    say(f"OCR tahap 2: {len(todo)} halaman dari {n_docs} dokumen · batch {a.batch} · "
        f"hentikan kapan saja dengan Ctrl+C (lanjut otomatis nanti)", "bold")
    if not todo:
        return

    from transformers import AutoModelForImageTextToText, AutoProcessor
    model = AutoModelForImageTextToText.from_pretrained(MODEL, torch_dtype=torch.bfloat16).to("cuda").eval()
    proc = AutoProcessor.from_pretrained(MODEL)
    proc.tokenizer.padding_side = "left"
    say(f"Model dimuat · VRAM {torch.cuda.memory_allocated()/1e9:.1f} GB")

    def run(batch):
        imgs = [render(root / f, pg) for _, f, pg in batch]
        msgs = [[{"role": "user", "content": [{"type": "image", "image": im}, {"type": "text", "text": "OCR:"}]}] for im in imgs]
        inp = proc.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True, return_dict=True,
                                       return_tensors="pt", padding=True).to("cuda")
        with torch.inference_mode():
            kw = {"repetition_penalty": rep_pen} if rep_pen else {}
            out = model.generate(**inp, max_new_tokens=max_tok, do_sample=False, use_cache=True, **kw)
        L = inp["input_ids"].shape[-1]
        res = []
        for i in range(len(batch)):
            ids = out[i][L:]
            ntok = int((ids != proc.tokenizer.pad_token_id).sum())
            res.append((proc.decode(ids, skip_special_tokens=True).strip(), ntok))
        return res

    done, t0, bs = 0, time.time(), a.batch
    cur_doc = None
    with Progress(TextColumn("[bold]{task.description}"), BarColumn(), MofNCompleteColumn(), TextColumn("·"),
                  TimeElapsedColumn(), TextColumn("· sisa"), TimeRemainingColumn(), console=console) as prog:
        task = prog.add_task("OCR", total=len(todo))
        i = 0
        try:
            while i < len(todo):
                if stop_file.exists():
                    say("File STOP ditemukan → berhenti rapi.", "yellow"); stop_file.unlink(); break
                batch = todo[i:i + bs]
                if batch[0][0] != cur_doc:
                    cur_doc = batch[0][0]
                prog.update(task, description=f"{batch[0][1][:40]:40}")
                tb = time.time()
                try:
                    res = run(batch)
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    if bs > 1:
                        bs = max(1, bs // 2); say(f"VRAM penuh → batch diturunkan ke {bs}", "yellow"); continue
                    raise
                dt = (time.time() - tb) / len(batch)
                for (sha, f, pg), (text, ntok) in zip(batch, res):
                    d = out_root / sha
                    d.mkdir(parents=True, exist_ok=True)
                    hit_limit = ntok >= max_tok - 2
                    looped = False
                    if hit_limit:                       # macet berulang hanya bila mentok batas DAN berpola
                        text, looped = trim(text)
                    rec = {"doc": sha, "file": f, "pdf_page": pg, "engine": ENGINE, "engine_version": ENGINE_VERSION,
                           "text": text, "tokens": ntok, "truncated": hit_limit and not looped, "loop_trimmed": looped,
                           "sec_per_page": round(dt, 1), "dpi": DPI, "max_pixels": MAX_PIXELS,
                           "ocr_at": datetime.now().isoformat(timespec="seconds")}
                    if a.redo:
                        old = json.loads((d / f"{pg}.json").read_text(encoding="utf-8"))
                        better = redo_quality(rec) > redo_quality(old)
                        rec = (rec if better else old) | {"redo": True, "redo_improved": better,
                                                          "text_prev": old["text"] if better else text}
                        if not better:
                            rec["sec_per_page"] = old.get("sec_per_page")
                    tmp = d / f"{pg}.json.tmp"
                    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
                    tmp.replace(d / f"{pg}.json")
                    if looped:
                        say(f"[!] {f} hlm {pg}: OCR macet berulang → bagian berulang dipotong, ditandai", "yellow")
                    elif rec["truncated"]:
                        say(f"[!] {f} hlm {pg}: halaman sangat panjang, keluaran terpotong — ditandai", "yellow")
                done += len(batch); i += len(batch)
                prog.advance(task, len(batch))
                if done % (bs * 10) == 0:
                    say(f"{done}/{len(todo)} hlm · {(time.time()-t0)/done:.1f} dtk/hlm rata-rata")
        except KeyboardInterrupt:
            say("Dihentikan pengguna. Hasil yang sudah selesai tersimpan; jalankan lagi untuk melanjutkan.", "yellow")
    el = time.time() - t0
    say(f"SELESAI sesi: {done} halaman dalam {el/60:.1f} menit ({el/max(1,done):.1f} dtk/hlm). "
        f"Jalankan 'rag.library build' untuk memasukkan hasil ke indeks.", "bold green")


if __name__ == "__main__":
    main()
