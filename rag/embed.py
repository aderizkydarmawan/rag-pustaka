"""Tahap 3: embedding (pencarian makna) semua chunk dengan Qwen3-Embedding-4B (GPU, 4-bit).

- Cache per teks chunk: .rag/index/emb/part_*.npz (kunci = sha1 header+teks). Bisa dihentikan
  kapan saja (Ctrl+C / file .rag/STOP) dan dilanjutkan; buku baru cukup menambah yang belum ada.
- Selesai → kolom `vector` ditempel ke tabel chunks (library.build juga menempelkannya dari cache).

Pemakaian:  python -m rag.embed "<folder>" [--batch-tokens 6144]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import MODELS_DIR
MODEL = str(MODELS_DIR / "Qwen__Qwen3-Embedding-4B")
EMBED_NAME = "Qwen3-Embedding-4B@5cf2132 nf4/bf16 last-token L2"
MAX_LEN = 1024
DIM = 2560
QUERY_INSTRUCT = "Given a research question, retrieve passages from books and papers that answer it"


def key_of(header: str, text: str) -> str:
    return hashlib.sha1((header + "\n" + text).encode("utf-8")).hexdigest()


def doc_text(header: str, text: str) -> str:
    return f"{header}\n{text}"


def query_text(q: str) -> str:
    return f"Instruct: {QUERY_INSTRUCT}\nQuery:{q}"


def emb_dir(root: Path) -> Path:
    return root / ".rag" / "index" / "emb"


def load_cache(root: Path) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for f in sorted(emb_dir(root).glob("part_*.npz")):
        z = np.load(f)
        for k, v in zip(z["keys"], z["vecs"]):
            out[str(k)] = v
    return out


def load_model():
    import torch
    from transformers import AutoModel, AutoTokenizer, BitsAndBytesConfig
    tok = AutoTokenizer.from_pretrained(MODEL, padding_side="left")
    model = AutoModel.from_pretrained(MODEL, quantization_config=BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16),
        torch_dtype=torch.bfloat16, device_map="cuda").eval()
    return tok, model


def encode(tok, model, texts: list[str]) -> np.ndarray:
    import torch
    enc = tok(texts, padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        h = model(**enc).last_hidden_state[:, -1]          # padding kiri → token terakhir
        h = torch.nn.functional.normalize(h.float(), dim=-1)
    return h.cpu().numpy().astype(np.float16)


def attach(root: Path, cache: dict | None = None) -> int:
    """Tempel kolom vector ke tabel chunks dari cache. Mengembalikan jumlah chunk ber-vector."""
    import lancedb
    import pyarrow as pa
    cache = cache if cache is not None else load_cache(root)
    db = lancedb.connect(str(root / ".rag" / "index" / "lancedb"))
    t = db.open_table("chunks").to_arrow()
    t = t.drop([c for c in ("vector", "has_vector") if c in t.column_names])
    zero = np.zeros(DIM, dtype=np.float32)
    vecs, n = [], 0
    for h, x in zip(t.column("header").to_pylist(), t.column("text").to_pylist()):
        v = cache.get(key_of(h, x))
        n += v is not None
        vecs.append(zero if v is None else v.astype(np.float32))
    arr = pa.FixedSizeListArray.from_arrays(pa.array(np.concatenate(vecs), pa.float32()), DIM)
    t = t.append_column("vector", arr).append_column("has_vector", pa.array([bool(np.any(v)) for v in vecs]))
    db.create_table("chunks", data=t, mode="overwrite")
    tb = db.open_table("chunks")
    tb.create_fts_index("text", replace=True, stem=True, remove_stop_words=True)
    mf = root / ".rag" / "manifest.json"
    m = json.loads(mf.read_text(encoding="utf-8"))
    m["dense"] = dict(model=EMBED_NAME, dim=DIM, max_len=MAX_LEN, query_instruct=QUERY_INSTRUCT,
                      n_with_vector=n, attached_at=datetime.now().isoformat(timespec="seconds"))
    mf.write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
    return n


def main() -> None:
    from rich.console import Console
    from rich.progress import (BarColumn, MofNCompleteColumn, Progress, TextColumn,
                               TimeElapsedColumn, TimeRemainingColumn)
    import lancedb
    import torch
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--batch-tokens", type=int, default=6144)
    a = ap.parse_args()
    root = Path(a.folder).resolve()
    con = Console()
    stop_file = root / ".rag" / "STOP"
    log = open(root / ".rag" / "logs" / "embed.log", "a", encoding="utf-8")

    def say(msg, style=""):
        s = datetime.now().strftime("%H:%M:%S")
        log.write(f"{s} {msg}\n"); log.flush()
        con.print(f"[dim]{s}[/] {msg}", style=style)

    emb_dir(root).mkdir(parents=True, exist_ok=True)
    cache = load_cache(root)
    t = lancedb.connect(str(root / ".rag" / "index" / "lancedb")).open_table("chunks").to_arrow()
    todo, seen = [], set()
    for h, x in zip(t.column("header").to_pylist(), t.column("text").to_pylist()):
        k = key_of(h, x)
        if k not in cache and k not in seen:
            seen.add(k); todo.append((k, doc_text(h, x)))
    say(f"Embedding: {len(todo)} chunk baru · {len(cache)} sudah di cache · hentikan kapan saja dengan Ctrl+C", "bold")
    if todo:
        tok, model = load_model()
        say(f"Model dimuat (4-bit) · VRAM {torch.cuda.memory_allocated()/1e9:.1f} GB")
        lens = [len(tok(x, truncation=True, max_length=MAX_LEN)["input_ids"]) for _, x in todo]
        order = sorted(range(len(todo)), key=lambda i: -lens[i])       # panjang dulu → OOM ketahuan di awal
        batches, cur, mx = [], [], 0
        for i in order:
            m2 = max(mx, lens[i])
            if cur and m2 * (len(cur) + 1) > a.batch_tokens:
                batches.append(cur); cur, m2 = [], lens[i]
            cur.append(i); mx = m2
        if cur:
            batches.append(cur)
        part = len(list(emb_dir(root).glob("part_*.npz")))
        buf_k, buf_v, done, t0 = [], [], 0, time.time()

        def save():
            nonlocal buf_k, buf_v, part
            if buf_k:
                np.savez(emb_dir(root) / f"part_{part:05d}.npz", keys=np.array(buf_k), vecs=np.stack(buf_v))
                for k, v in zip(buf_k, buf_v):
                    cache[k] = v
                part += 1; buf_k, buf_v = [], []

        with Progress(TextColumn("[bold]Embedding"), BarColumn(), MofNCompleteColumn(), TextColumn("·"),
                      TimeElapsedColumn(), TextColumn("· sisa"), TimeRemainingColumn(), console=con) as prog:
            task = prog.add_task("e", total=len(todo))
            bi = 0
            try:
                while bi < len(batches):
                    if stop_file.exists():
                        say("File STOP ditemukan → berhenti rapi.", "yellow"); stop_file.unlink(); break
                    b = batches[bi]
                    try:
                        vecs = encode(tok, model, [todo[i][1] for i in b])
                    except torch.cuda.OutOfMemoryError:
                        torch.cuda.empty_cache()
                        if len(b) == 1:
                            raise
                        h = len(b) // 2
                        batches[bi:bi + 1] = [b[:h], b[h:]]
                        say(f"VRAM penuh → batch dipecah ({len(b)} → {h})", "yellow"); continue
                    for i, v in zip(b, vecs):
                        buf_k.append(todo[i][0]); buf_v.append(v)
                    done += len(b); bi += 1
                    prog.advance(task, len(b))
                    if len(buf_k) >= 500:
                        save(); say(f"{done}/{len(todo)} chunk · tersimpan")
            except KeyboardInterrupt:
                say("Dihentikan pengguna. Hasil tersimpan; jalankan lagi untuk melanjutkan.", "yellow")
            save()
        el = time.time() - t0
        say(f"{done} chunk dalam {el/60:.1f} menit ({done/max(el,1):.1f} chunk/dtk)")
        del model; torch.cuda.empty_cache()
    n = attach(root, cache)
    from .library import compact
    compact(root)
    say(f"SELESAI embedding: {n}/{t.num_rows} chunk punya vector, ditempel ke indeks.", "bold green")


if __name__ == "__main__":
    main()
