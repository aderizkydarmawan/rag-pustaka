import json, re, time, urllib.parse, urllib.request, pandas as pd
df = pd.read_csv("trial_results.csv", keep_default_na=False)
def get(url):
    for k in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "rag-local-trial/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429: raise
            time.sleep(5 * (k + 1))
    raise RuntimeError("429 terus")
for i, d in df.iterrows():
    if d.via == "doi": continue
    q = re.sub(r"\b(1[5-9]|20)\d\d\b|\bdkk\b", " ", d.query); q = re.sub(r"\s+", " ", q).strip()
    try:
        res = get("https://api.openalex.org/works?per-page=3&search=" + urllib.parse.quote(q))["results"]
        w = res[0] if res else {}
        df.loc[i, ["query", "via", "oa_title", "oa_year", "oa_doi", "oa_type", "oa_retracted", "oa_authors"]] = [
            q, "title", w.get("display_name"), w.get("publication_year"), w.get("doi"), w.get("type"), w.get("is_retracted"),
            "; ".join(a["author"]["display_name"] for a in w.get("authorships", [])[:3])]
        df.loc[i, "top3"] = " || ".join(f"{r['display_name']} ({r.get('publication_year')})" for r in res)
    except Exception as e:
        df.loc[i, "via"] = f"error {e}"
    time.sleep(2)
df.to_csv("trial_results.csv", index=False, encoding="utf-8")
for _, d in df[df.via != "doi"].iterrows():
    print(f"{d.file[:55]:55} | {d.oa_title} ({d.oa_year}) | {d.oa_authors[:40]}")
