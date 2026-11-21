"""OCR ulang halaman bertanda (terpotong / macet berulang) dengan membelah halaman jadi dua.

Alasan: halaman padat (tabel panjang, banyak rumus) melebihi batas token atau membuat model
macet berulang. Dua potongan → tiap potongan berisi separuh teks dan, karena batas piksel sama,
resolusinya kira-kira 1,4× lebih tajam.

- Garis belah dicari di baris paling kosong dekat tengah halaman (tidak memotong baris teks).
- Tiap potongan dipotong sisa pengulangannya (loopdetect.trim).
- Hasil baru dipakai HANYA bila lebih baik menurut ocr.redo_quality; hasil lama disimpan di
  `text_before_split`. Halaman yang sudah dicoba diberi `split_tried` sehingga tidak diulang.
- Bisa dihentikan kapan saja (Ctrl+C atau file .rag/STOP).

Pemakaian:  python -m rag.ocr_split "<folder>" [--batch 2] [--max-pages N]
"""
from __future__ import annotations

import argparse
import io
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pymupdf
import torch
from PIL import Image
from rich.console import Console

from .loopdetect import trim
from .ocr import ENGINE, ENGINE_VERSION, MAX_PIXELS, MODEL, redo_quality

MAX_NEW_TOKENS = 2048
DPI = 200
console = Console()


def flagged_pages(root: Path) -> list[Path]:
    out = []
    for f in sorted((root / ".rag" / "extract" / "ocr").glob("*/*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        if (r.get("truncated") or r.get("loop_trimmed")) and not r.get("split_tried"):
            out.append(f)
    return out


def _fit(img: Image.Image) -> Image.Image:
    s = (MAX_PIXELS / (img.width * img.height)) ** 0.5
    if s < 1:
        img = img.resize((max(28, int(img.width * s) // 28 * 28), max(28, int(img.height * s) // 28 * 28)), Image.LANCZOS)
    return img


def halves(pdf: Path, pg: int, parts: int = 2) -> list[Image.Image]:
    """Belah halaman jadi `parts` potongan di pita paling kosong dekat tiap garis k/parts."""
    pix = pymupdf.open(pdf)[pg - 1].get_pixmap(dpi=DPI)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    g = np.asarray(img.convert("L"), dtype=np.float32)
    ink = (g < 160).sum(axis=1)                       # jumlah piksel gelap per baris
    h = len(ink)
    win = np.convolve(ink, np.ones(9) / 9, mode="same")  # cari pita kosong, bukan satu baris
    cuts, band = [0], 0.10 / max(1, parts - 1) if parts > 2 else 0.10
    for k in range(1, parts):
        c = k / parts
        lo, hi = int(h * (c - band)), int(h * (c + band))
        cuts.append(lo + int(np.argmin(win[lo:hi])))
    cuts.append(h)
    return [_fit(img.crop((0, a, img.width, b))) for a, b in zip(cuts, cuts[1:])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--max-pages", type=int, default=0)
    ap.add_argument("--parts", type=int, default=2, help="jumlah potongan per halaman")
    ap.add_argument("--pages", nargs="*", default=None,
                    help="hanya halaman tertentu, format <sha12>:<pdf_page>; mengabaikan tanda split_tried")
    a = ap.parse_args()
    root = Path(a.folder).resolve()
    stop_file = root / ".rag" / "STOP"
    log = open(root / ".rag" / "logs" / "ocr.log", "a", encoding="utf-8")

    def say(msg, style=""):
        s = datetime.now().strftime("%H:%M:%S")
        log.write(f"{s} [split] {msg}\n"); log.flush()
        console.print(f"[dim]{s}[/] {msg}", style=style)

    if a.pages:
        todo = []
        for spec in a.pages:
            sha, pg = spec.split(":")
            todo += sorted((root / ".rag" / "extract" / "ocr").glob(f"{sha}*/{int(pg)}.json"))
    else:
        todo = flagged_pages(root)
    if a.max_pages:
        todo = todo[: a.max_pages]
    say(f"OCR belah-dua: {len(todo)} halaman bertanda · batch {a.batch} potongan · Ctrl+C untuk berhenti", "bold")
    if not todo:
        return

    from transformers import AutoModelForImageTextToText, AutoProcessor
    model = AutoModelForImageTextToText.from_pretrained(MODEL, torch_dtype=torch.bfloat16).to("cuda").eval()
    proc = AutoProcessor.from_pretrained(MODEL)
    proc.tokenizer.padding_side = "left"
    say(f"Model dimuat · VRAM {torch.cuda.memory_allocated()/1e9:.1f} GB")

    def gen(imgs):
        msgs = [[{"role": "user", "content": [{"type": "image", "image": im}, {"type": "text", "text": "OCR:"}]}] for im in imgs]
        inp = proc.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True, return_dict=True,
                                       return_tensors="pt", padding=True).to("cuda")
        with torch.inference_mode():
            out = model.generate(**inp, max_new_tokens=MAX_NEW_TOKENS, do_sample=False, use_cache=True,
                                 repetition_penalty=1.05)
        L = inp["input_ids"].shape[-1]
        res = []
        for i in range(len(imgs)):
            ids = out[i][L:]
            ntok = int((ids != proc.tokenizer.pad_token_id).sum())
            res.append((proc.decode(ids, skip_special_tokens=True).strip(), ntok))
        return res

    t0, won, n = time.time(), 0, 0
    try:
        for f in todo:
            if stop_file.exists():
                say("File STOP ditemukan → berhenti rapi.", "yellow"); stop_file.unlink(); break
            old = json.loads(f.read_text(encoding="utf-8"))
            tp = time.time()
            imgs = halves(root / old["file"], old["pdf_page"], a.parts)
            parts = []
            for i in range(0, len(imgs), a.batch):
                try:
                    parts += gen(imgs[i:i + a.batch])
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    parts += [gen([im])[0] for im in imgs[i:i + a.batch]]
            texts, any_trunc, any_loop, ntok = [], False, False, 0
            for text, k in parts:
                ntok += k
                if k >= MAX_NEW_TOKENS - 2:
                    text, looped = trim(text)
                    any_loop |= looped
                    any_trunc |= not looped
                texts.append(text)
            new = {"text": "\n\n".join(t for t in texts if t), "truncated": any_trunc, "loop_trimmed": any_loop}
            better = redo_quality(new) > redo_quality(old)
            rec = dict(old)
            if better:
                rec.update(new)
                rec.update({"text_before_split": old["text"], "engine": ENGINE, "engine_version": ENGINE_VERSION,
                            "tokens": ntok, "dpi": DPI, "split": a.parts,
                            "ocr_at": datetime.now().isoformat(timespec="seconds")})
                won += 1
            rec.update({"split_tried": datetime.now().isoformat(timespec="seconds"), "split_improved": better})
            tmp = f.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            tmp.replace(f)
            n += 1
            q0, q1 = redo_quality(old), redo_quality(new)
            flag = "bersih" if q1[0] else ("macet" if new["loop_trimmed"] else "terpotong")
            say(f"{n}/{len(todo)} {old['file'][:45]} hlm {old['pdf_page']}: kata berbeda {q0[1]}→{q1[1]} ({flag}) "
                f"→ {'DIPAKAI' if better else 'lama dipertahankan'} · {time.time()-tp:.0f} dtk",
                "green" if better else "")
    except KeyboardInterrupt:
        say("Dihentikan (Ctrl+C). Jalankan lagi untuk melanjutkan.", "yellow")
    say(f"SELESAI: {n} halaman dicoba, {won} membaik · {(time.time()-t0)/60:.1f} menit", "bold green")


if __name__ == "__main__":
    main()
