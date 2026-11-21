"""Uji pencarian RAG dengan soal berkunci jawaban (eval/questions_v1.json).

Jalankan dari akar repo:  python eval/run_eval.py   (env RAG_EVAL_LIBRARY = nama pustaka terdaftar)

Relevansi dinilai otomatis: sebuah chunk "menjawab" bila berasal dari dokumen yang diterima
(docs; kosong = dokumen mana pun) DAN teksnya cocok dengan regex kunci (ans).
"""
import hashlib
import json
import random
import re
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag import server  # noqa: E402

HERE = Path(__file__).resolve().parent
import os
LIB = os.environ.get("RAG_EVAL_LIBRARY", "PUSTAKA MATEMATIKA-AI")
SEED = 42
N_BOOT = 10000

# jangan mengotori log pemakaian, dan ambil teks chunk utuh untuk dinilai
server._log = lambda *a, **k: None
server.MAX_WORDS_PER_HIT = 10**6
server.MAX_WORDS_PER_CALL = 10**9

CONFIGS = [  # (nama, mode, rerank, k)
    ("bm25", "bm25", False, 10),
    ("dense", "dense", False, 10),
    ("hybrid", "hybrid", False, 10),
    ("hybrid_k30", "hybrid", False, 30),
    ("hybrid_rerank", "hybrid", True, 10),
]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = re.sub(r"-\s*\n\s*", "", s)
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", s).lower()


def judge(q: dict, hit: dict) -> tuple[bool, bool]:
    f = hit["file"].lower()
    doc_ok = (not q["docs"]) or any(d.lower() in f for d in q["docs"])
    ans_ok = bool(q["ans"]) and doc_ok and re.search(q["ans"], norm(hit["text"])) is not None
    return doc_ok, ans_ok


def first_rank(flags: list[bool]) -> int | None:
    for i, x in enumerate(flags, 1):
        if x:
            return i
    return None


def boot_ci(vals: list[float], rng: random.Random) -> tuple[float, float]:
    n = len(vals)
    means = sorted(sum(vals[rng.randrange(n)] for _ in range(n)) / n for _ in range(N_BOOT))
    return means[int(0.025 * N_BOOT)], means[int(0.975 * N_BOOT)]


def main() -> None:
    qpath = HERE / "questions_v1.json"
    raw = qpath.read_bytes()
    qhash = hashlib.sha256(raw).hexdigest()
    qs = json.loads(raw)["questions"]
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out = HERE / "results" / run_id
    out.mkdir(parents=True, exist_ok=True)
    root = server._resolve(LIB)
    manifest = json.loads((root / ".rag" / "manifest.json").read_text(encoding="utf-8"))
    print(f"run {run_id} | {len(qs)} soal | sha256 soal {qhash[:12]} | chunk {manifest['n_chunks']}", flush=True)

    rows = []
    for name, mode, rerank, k in CONFIGS:
        print(f"\n== {name} ==", flush=True)
        for i, q in enumerate(qs, 1):
            t0 = time.time()
            res = server.search(q["q"], library=LIB, k=k, mode=mode, rerank=rerank)
            dt = time.time() - t0
            hits = res["results"]
            js = [judge(q, h) for h in hits]
            rec = {
                "config": name, "id": q["id"], "cat": q["cat"], "sec": round(dt, 2),
                "mode_used": res["params_used"]["mode"], "notes": res.get("notes"),
                "doc_rank": first_rank([d for d, _ in js]) if q["docs"] else None,
                "ans_rank": first_rank([a for _, a in js]) if q["ans"] else None,
                "top_rerank": hits[0]["scores"].get("rerank") if hits else None,
                "hits": [{"rank": h["rank"], "file": h["file"], "chunk_id": h["chunk_id"],
                          "pdf_page": h.get("pdf_page_start") or h.get("pdf_page"),
                          "doc_ok": d, "ans_ok": a, "scores": h["scores"], "flag": h["quality_flag"]}
                         for h, (d, a) in zip(hits, js)],
            }
            rows.append(rec)
            print(f"{i:>2}/{len(qs)} {q['id']} doc@{rec['doc_rank']} ans@{rec['ans_rank']} "
                  f"rr={rec['top_rerank']} {dt:.1f}s", flush=True)
    (out / "raw.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    rng = random.Random(SEED)
    summary = {"run_id": run_id, "questions_sha256": qhash, "n_questions": len(qs), "seed": SEED,
               "library": LIB, "n_chunks": manifest["n_chunks"], "dense": manifest.get("dense"),
               "built_at": manifest.get("built_at"), "configs": {}}

    def hit_at(rank, k):
        return 1.0 if rank is not None and rank <= k else 0.0

    def rr(rank, k=10):
        return 1.0 / rank if rank is not None and rank <= k else 0.0

    for name, *_ in CONFIGS:
        rs = [r for r in rows if r["config"] == name]
        cfg = {}
        for cat in ["A", "B", "C", "ABC"]:
            sub = [r for r in rs if r["cat"] in cat]
            m = {"n": len(sub)}
            ks = (1, 3, 10, 30) if name == "hybrid_k30" else (1, 3, 10)
            for k in ks:
                v = [hit_at(r["ans_rank"], k) for r in sub]
                lo, hi = boot_ci(v, rng)
                m[f"ans@{k}"] = [round(sum(v) / len(v), 3), round(lo, 3), round(hi, 3)]
            v = [rr(r["ans_rank"]) for r in sub]
            lo, hi = boot_ci(v, rng)
            m["ans_mrr@10"] = [round(sum(v) / len(v), 3), round(lo, 3), round(hi, 3)]
            subd = [r for r in sub if r["cat"] in "AC"]
            if subd:
                for k in ks:
                    v = [hit_at(r["doc_rank"], k) for r in subd]
                    m[f"doc@{k}"] = round(sum(v) / len(v), 3)
                m["n_doc"] = len(subd)
            m["median_sec"] = sorted(r["sec"] for r in sub)[len(sub) // 2]
            cfg[cat] = m
        summary["configs"][name] = cfg

    # selisih berpasangan (bootstrap per soal) terhadap bm25, metrik ans_mrr@10 dan ans@10
    base = {r["id"]: r for r in rows if r["config"] == "bm25" and r["cat"] != "D"}
    paired = {}
    for name in ["dense", "hybrid", "hybrid_rerank"]:
        cur = {r["id"]: r for r in rows if r["config"] == name and r["cat"] != "D"}
        for metric, fn in [("ans_mrr@10", rr), ("ans@10", lambda x: hit_at(x, 10)), ("ans@1", lambda x: hit_at(x, 1))]:
            d = [fn(cur[i]["ans_rank"]) - fn(base[i]["ans_rank"]) for i in base]
            lo, hi = boot_ci(d, rng)
            paired[f"{name}-bm25 {metric}"] = [round(sum(d) / len(d), 3), round(lo, 3), round(hi, 3)]
    summary["paired_vs_bm25"] = paired

    # skor rerank teratas: soal berjawaban vs soal di luar koleksi
    rrs = [r for r in rows if r["config"] == "hybrid_rerank"]
    summary["top_rerank_score"] = {
        "answerable": sorted(r["top_rerank"] for r in rrs if r["cat"] != "D" and r["top_rerank"] is not None),
        "out_of_collection": {r["id"]: r["top_rerank"] for r in rrs if r["cat"] == "D"},
    }
    summary["failures_hybrid_rerank"] = [
        {"id": r["id"], "doc_rank": r["doc_rank"], "ans_rank": r["ans_rank"],
         "top3": [h["file"][:60] for h in r["hits"][:3]]}
        for r in rrs if r["cat"] != "D" and (r["ans_rank"] is None or r["ans_rank"] > 3)]
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + json.dumps({k: v for k, v in summary.items() if k != "top_rerank_score"}, ensure_ascii=False, indent=1))
    print(f"\nSELESAI. Hasil: {out}", flush=True)


if __name__ == "__main__":
    main()
