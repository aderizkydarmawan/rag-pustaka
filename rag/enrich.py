"""Metadata OpenAlex HANYA lewat DOI (SPEC §16, 2026-11-18). Lookup judul sengaja tidak ada (uji: 0/15 benar).

- Hanya berjalan bila .rag/settings.json berisi lookup = "doi".
- Yang dikirim: DOI saja. Hasil di-cache di .rag/extract/openalex/<sha>.json (sekali per dokumen).
- DOI hasil harus sama dengan DOI yang ditanyakan; kalau tidak, dicatat 'mismatch' dan tidak dipakai.
- Dokumen tanpa DOI: retraction = "belum dicek".

Pemakaian:  python -m rag.enrich "<folder>" [--refresh]   lalu  python -m rag.library build "<folder>"
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import settings

API = "https://api.openalex.org/works/doi:"
UA = {"User-Agent": "rag-pustaka-local/1.0"}


def _get(doi: str) -> dict | None:
    for k in range(5):
        try:
            req = urllib.request.Request(API + urllib.parse.quote(doi), headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code != 429:
                raise
            time.sleep(5 * (k + 1))
    raise RuntimeError("OpenAlex terus menolak (429)")


def lookup(root: Path, refresh: bool = False) -> None:
    s = settings.load(root)
    if not s or s.get("lookup") != "doi":
        print(f"Lookup tidak dijalankan: pengaturan folder = {s} (perlu lookup='doi').")
        return
    ext = root / ".rag" / "extract"
    cache = ext / "openalex"
    cache.mkdir(exist_ok=True)
    n = ok = miss = 0
    for mp in sorted(ext.glob("*.meta.json")):
        meta = json.loads(mp.read_text(encoding="utf-8"))
        doi = (meta.get("doi") or "").strip().lower()
        if not doi:
            continue
        f = cache / f"{meta['sha256']}.json"
        if f.exists() and not refresh:
            continue
        n += 1
        try:
            w = _get(doi)
        except Exception as e:  # noqa: BLE001
            print(f"[galat] {meta['rel_path']}: {e}")
            continue
        got = ((w or {}).get("doi") or "").lower().replace("https://doi.org/", "")
        status = "not_found" if w is None else ("ok" if got == doi else "mismatch")
        rec = {"queried_doi": doi, "status": status, "checked_at": datetime.now().isoformat(timespec="seconds")}
        if status == "ok":
            rec.update(openalex_id=w["id"], title=w.get("display_name"), year=w.get("publication_year"),
                       authors=[a["author"]["display_name"] for a in w.get("authorships", [])],
                       venue=((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                       type=w.get("type"), is_retracted=bool(w.get("is_retracted")))
            ok += 1
        else:
            miss += 1
        f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{status:9} {meta['rel_path'][:70]}" + ("  [DITARIK!]" if rec.get("is_retracted") else ""))
        time.sleep(0.2)
    print(f"SELESAI lookup DOI: {n} ditanyakan, {ok} cocok, {miss} tidak ditemukan/tidak cocok. "
          f"Jalankan 'rag.library build' agar masuk indeks.")


def for_doc(root: Path, sha: str) -> dict:
    """Ringkasan untuk indeks: retracted ('true'/'false'/'belum dicek') + metadata OpenAlex bila ada."""
    f = Path(root) / ".rag" / "extract" / "openalex" / f"{sha}.json"
    try:
        r = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"retracted": "belum dicek", "authors": "", "oa_year": 0, "meta_source": "nama file"}
    if r.get("status") != "ok":
        return {"retracted": "belum dicek", "authors": "", "oa_year": 0,
                "meta_source": f"nama file (DOI {r.get('status')})"}
    return {"retracted": "true" if r["is_retracted"] else "false", "authors": "; ".join(r["authors"][:8]),
            "oa_year": int(r.get("year") or 0), "meta_source": f"OpenAlex via DOI ({r['checked_at'][:10]})"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--refresh", action="store_true", help="tanya ulang (mis. cek retraction terbaru)")
    a = ap.parse_args()
    lookup(Path(a.folder).resolve(), a.refresh)


if __name__ == "__main__":
    main()
