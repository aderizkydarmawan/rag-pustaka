import json, os, time
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
from huggingface_hub import HfApi, snapshot_download
api = HfApi()
REPOS = ["PaddlePaddle/PaddleOCR-VL-1.6", "opendatalab/MinerU2.5-Pro-2605-1.2B",
         "Qwen/Qwen3-Embedding-4B", "Qwen/Qwen3-Reranker-4B"]
out = {}
for r in REPOS:
    info = api.model_info(r)
    lic = (info.card_data or {}).get("license") if info.card_data else None
    t = time.time()
    print(f"[UNDUH] {r} (lisensi: {lic}, revisi {info.sha[:10]})", flush=True)
    p = snapshot_download(r, revision=info.sha, local_dir=os.path.join("models", r.replace("/", "__")))
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(p) for f in fs) / 1e9
    print(f"[OK] {r}: {size:.1f} GB dalam {time.time()-t:.0f} dtk", flush=True)
    out[r] = {"revision": info.sha, "license": lic, "path": p, "size_gb": round(size, 2)}
    json.dump(out, open("models/models.json", "w"), indent=1)
print("SELESAI semua model diunduh")
