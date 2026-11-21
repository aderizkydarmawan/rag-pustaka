"""Uji coba lookup OpenAlex pada 20 dokumen (2026-11-18). Hanya membaca indeks; tidak mengubah apa pun.
Kueri: DOI bila ada, kalau tidak judul dari nama file (tahun/kata 'dkk', 'buku', 'PREVIEW' dibuang)."""
import json, re, time, urllib.parse, urllib.request, lancedb, pandas as pd
import sys
LIB = sys.argv[1]                      # folder pustaka ber-indeks
OUT = str(__import__("pathlib").Path(__file__).resolve().parent)
t = lancedb.connect(LIB + r"\.rag\index\lancedb").open_table("docs").to_pandas()
t["has_doi"] = t.doi != ""
t["old"] = (t.year > 0) & (t.year < 1960)
strata = [t[t.has_doi], t[~t.has_doi & (t.type == "paper") & ~t.old], t[~t.has_doi & (t.type == "book") & ~t.old], t[t.old & ~t.has_doi]]
n = [5, 5, 5, 5]
s = pd.concat([d.sample(min(k, len(d)), random_state=42) for d, k in zip(strata, n)])
def q_from_file(f):
    x = re.sub(r"\.pdf$", "", f.split("/")[-1].split("\\")[-1])
    x = re.sub(r"_(PREVIEW|RESENSI|KUNCI_JAWABAN|BUKAN_BUKUNYA|buku|dkk|\d+hlm\w*|\d+e|1e|2e|3e)\b", " ", x, flags=re.I)
    x = re.sub(r"\b(1[5-9]|20)\d\d\b", " ", x).replace("_", " ")
    return re.sub(r"\s+", " ", x).strip()
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "rag-local-trial/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
rows = []
for _, d in s.iterrows():
    try:
        if d.doi:
            w = get("https://api.openalex.org/works/doi:" + urllib.parse.quote(d.doi)); via = "doi"; q = d.doi
        else:
            q = q_from_file(d.file)
            res = get("https://api.openalex.org/works?per-page=1&search=" + urllib.parse.quote(q))["results"]
            w = res[0] if res else None; via = "title"
    except Exception as e:
        w, via = None, f"error {e}"
    rows.append(dict(file=d.file, idx_year=int(d.year), idx_doi=d.doi, query=q, via=via,
        oa_title=(w or {}).get("display_name"), oa_year=(w or {}).get("publication_year"),
        oa_doi=(w or {}).get("doi"), oa_type=(w or {}).get("type"), oa_retracted=(w or {}).get("is_retracted"),
        oa_authors="; ".join(a["author"]["display_name"] for a in (w or {}).get("authorships", [])[:3])))
    time.sleep(0.3)
pd.DataFrame(rows).to_csv(OUT + r"\trial_results.csv", index=False, encoding="utf-8")
for r in rows: print(json.dumps(r, ensure_ascii=False))
