# Uji lookup OpenAlex, 20 dokumen (2026-11-18)
Sampel berstrata (seed 42) dari 269 dokumen PUSTAKA MATEMATIKA-AI: 5 ber-DOI, 5 paper tanpa DOI, 5 buku tanpa DOI, 5 klasik (<1960).
Dikirim ke OpenAlex hanya DOI atau judul dari nama file. Script: `run_trial.py`, `retry.py`; data mentah: `trial_results.csv`.
Penilaian cocok/tidak oleh Claude dengan membandingkan judul & penulis hasil teratas dengan dokumen.

| Jalur | Benar | Catatan |
|---|---|---|
| DOI (5) | **5/5** | judul, penulis, tahun benar; Roediger–Butler: OpenAlex 2010 (online) vs nama file 2011 (cetak) |
| Judul (15) | **0/15** | hasil teratas selalu karya lain: resensi buku (Strauss, Karatzas–Shreve, Øksendal), retrospektif/studi sejarah (Markowitz, Bayes), paper lain yang menyebut judulnya (Valiant, Bellman, LeCun, Cox–Ross–Rubinstein, Fisher), paper lain sepenuhnya (Roberts–Tweedie, Curry→Goldstein 1965, Deisenroth), versi lain penulis sama (Bronstein 2017 bukan 2021); Bachelier: tidak ada hasil |

Catatan teknis: pencarian judul tanpa API key cepat kena HTTP 429; perlu jeda ≥2 dtk.

## Kesimpulan
- Lookup **lewat DOI aman dan berguna** (cuma 30/269 dokumen punya DOI di indeks).
- Lookup **lewat judul top-1 tidak boleh dipakai otomatis**: di koleksi ini 100% salah cocok dan akan menulis penulis/tahun yang salah dengan yakin (sesuai peringatan Contrarian di council).
- Bila judul tetap dipakai, harus ada pemeriksaan (penulis dan tahun dari nama file harus cocok) dan statusnya "perlu dicek", bukan ditulis langsung.
