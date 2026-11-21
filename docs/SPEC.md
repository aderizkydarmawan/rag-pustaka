# Spesifikasi RAG Pustaka Riset (v0.3, draf)

Tanggal: 2026-11-12 · Status: **draf, belum dibangun** · Dasar keputusan: `PROGRESS.md`, tiga verdict llm-council, laporan `~/.claude/skill-scout/scout-2026-11-11-rag.md`, hasil `methodology-review` atas v0.1

**Perubahan dari v0.2 (council 3, 2026-11-11):**
- **Versi 1 sengaja kecil** (§18) dan diuji pada PDF pengguna sebelum apa pun ditambah. Katalog §17 dibekukan sampai analisis kegagalan menunjuk ke sana.
- **Akses bertingkat**: koleksi → dokumen → bagian → potongan → halaman, ditambah `search_exact` (§8).
- **Kartu dokumen** (§5.6): kartu sederhana dibuat untuk **semua** dokumen, kartu lengkap dibuat bertahap. `source_spread` selalu dihitung terhadap **seluruh koleksi**.
- `search_counter` diganti menjadi **prosedur** (§9.1). Ditambah log pemakaian, cek sitasi saat jawaban ditulis, retraction, dan hash teks per halaman.
- **Metrik utama: recall@50** (Claude = pembaca akhir).
- **Server MCP dengan model tetap di GPU** (cadangan CPU).
- Ditambah: pemantauan proses oleh pengguna (§13.1), pemakaian berulang (§13.2), anggaran penyimpanan (§2.1), dan skala ribuan paper.

**Perubahan dari v0.1:** evaluasi (§10) ditulis ulang: soal dari pertanyaan nyata, pooling, dev/test terkunci, uji berpasangan, uji bertahap, uji ujung ke ujung. Ambang kualitas (§5.2) dikalibrasi, bukan ditebak. Uji OCR (§10.2) memakai sampel bertingkat dan kunci jawaban tanpa jangkar. Ada anggaran latensi (§7.1), pembekuan instruksi query (§6.3), dan `printed_page_confidence`. Ditambah **katalog opsi (§17)** berisi semua alternatif yang bisa diadu.

---

## 1. Tujuan

Claude Code bisa mencari ratusan paper dan buku (sebagian besar bahasa Inggris, banyak hasil scan) per folder topik dengan **akurasi setinggi mungkin**. Setiap hasil membawa sumber yang bisa dicek (file + halaman), dan aturan anti-bias di `CLAUDE.md` dijalankan oleh alat.

### Bukan tujuan
- Bukan chatbot atau UI sendiri. Yang menjawab dan bernalar tetap Claude.
- Bukan penentu arah riset (`CLAUDE.md` aturan 1).
- Kecepatan indexing tidak dioptimalkan. **Kecepatan query tetap punya batas** (§7.1).
- Belum ada distillation atau fine-tuning (tahap 2, bersyarat, §12).

## 2. Batasan

| Hal | Nilai |
|---|---|
| OS | Windows 11 |
| GPU | RTX 3070 Laptop, **8 GB VRAM** |
| RAM | ±15 GB |
| Python | 3.12 |
| Pengguna | Peneliti, bukan engineer; perawatan harus cukup dengan satu perintah |
| Jaringan | Indexing dan pencarian sepenuhnya lokal. Hanya unduhan model pertama yang butuh internet |
| Privasi | Isi PDF tidak dikirim ke layanan mana pun **kecuali** chunk yang dibaca Claude saat sesi (§11) |
| Disk | C: ±952 GB, kosong **369.9 GB** (dicek 2026-11-12); hanya satu drive, jadi backup ke media eksternal |
| Skala | Satu pustaka bisa berisi **ribuan paper** dengan tema berbeda; subfolder dipakai sebagai filter tema |
| Lokasi | Pustaka disalin ke folder **di luar** OneDrive/Google Drive, misalnya `C:\Pustaka\` |

### 2.1 Anggaran penyimpanan (perkiraan; dipastikan saat instalasi)
| Bagian | Perkiraan |
|---|---|
| Python + PyTorch CUDA + pustaka | 6–8 GB |
| OCR VLM + framework | 3–4 GB; Tesseract ±0.1 GB |
| Qwen3-Embedding-4B + reranker 4B | ±16 GB |
| **Alat, sekali pasang** | **±25–28 GB** |
| Per 1.000 paper (±15.000 halaman): lapis 1 + lapis 2 + log | ±1–2 GB |
| Sementara saat uji (8B + OCR kedua) | +30–35 GB, dihapus kalau kalah |

Gambar halaman tidak disimpan; dirender saat dibutuhkan. Contoh 3.000 paper: puncak ±60–70 GB saat uji, ±35 GB setelahnya.

### 2.2 Perkiraan waktu (belum diukur; diukur di langkah 0)
Untuk 1.000 paper:
- PDF digital: menit sampai ±1 jam.
- OCR: dengan 30% hasil scan, ±4.500 halaman × 2–5 detik = 3–6 jam.
- Embedding: 1–3 jam.

### 2.3 Apa yang lokal dan apa yang keluar
- **Lokal:** PDF, OCR, ekstraksi, indeks, model embedding dan reranker, pencarian, dan log. Indexing bisa berjalan tanpa internet.
- **Keluar ke Anthropic:** potongan teks yang dibaca Claude dan percakapan. Folder `private` hanya mengirim judul dan halaman.
- **Butuh internet:** unduhan model pertama kali, sesi Claude, dan lookup OpenAlex (opsional, mati secara bawaan).

## 3. Prinsip desain

1. **Dua lapis.** Lapis ekstraksi mahal dan disimpan permanen; lapis indeks murah dan bisa dibangun ulang.
2. **Satu jalur tetap dengan fallback.** Tidak ada "mastermind router". Fallback OCR diputuskan per halaman.
3. **Nilai bawaan dikunci.** Claude tidak mengatur bobot atau reranker. Setiap parameter yang dipakai dicatat di output.
4. **Aturan anti-bias dijalankan oleh kode.**
5. **Komponen baru hanya ditambahkan kalau uji pada set dev membuktikannya, lalu dikonfirmasi pada set test** (§10).
6. **Model dimuat bergantian**, tidak pernah dua model besar di VRAM sekaligus.
7. **Setiap angka harus punya asal-usul**: sitasi, kalibrasi pada data pengguna, atau dinyatakan sebagai "nilai awal yang akan diuji".
8. **Pencarian agentik**: Claude mencari 10–30 kali per tugas dan menjadi pembaca akhir. Recall dan kecepatan lebih penting daripada urutan sempurna.
9. **Satuan bukti adalah studi, bukan potongan teks.** Sebaran dihitung per studi dan kelompok penulis terhadap seluruh koleksi.
10. **Bias pembaca (Claude) juga harus terlihat**: apa yang dicari, apa yang muncul, apa yang dikutip, dan apa yang dibuang dicatat semua.
11. **Kecil dulu, buktikan, baru tambah.** Kerumitan adalah risiko terbesar bagi pengguna yang bukan engineer.
12. **Setiap proses panjang bisa dipantau langsung oleh pengguna** (§13.1).

## 4. Struktur folder

```
<Folder Topik>/
  *.pdf
  .rag/
    manifest.json                ← model, revisi, instruksi query, parameter; dicek setiap query
    extract/                     ← LAPIS 1 (permanen, WAJIB di-backup)
      <sha256>.jsonl
      <sha256>.meta.json
      _queue.json
      _quality_report.csv
    index/                       ← LAPIS 2 (bisa dibangun ulang)
      lancedb/
    logs/
      queries.jsonl              ← pencarian + parameter + hasil + chunk yang dipakai/dibuang Claude
    eval/
      questions_dev.jsonl        ← 70% soal, untuk memilih konfigurasi
      questions_test.jsonl       ← 30% soal, DIKUNCI (hash dicatat), dibuka sekali di akhir
      qrels.jsonl                ← penilaian relevansi hasil pooling
      ocr_gold/                  ← kunci jawaban uji OCR
      quality_labels.csv         ← label bagus/jelek untuk kalibrasi §5.2
      results/                   ← hasil + run_id (format experiment-log)

<Lokasi alat, sekali instal>
  rag/  models/  config.toml
```

## 5. Lapis 1: ekstraksi

### 5.1 Alur per file
1. Hitung **SHA-256**. Kalau hash dan versi parser sudah ada, file dilewati.
2. **Deteksi duplikat**:
   - Hash sama berarti file identik, dicatat sebagai alias.
   - DOI atau judul sama tapi hash berbeda berarti edisi atau versi lain, dan diberi `duplicate_group`.
3. Untuk setiap halaman: ambil lapisan teks PDF (PyMuPDF), lalu **klasifikasi jenis halaman** (§5.2), lalu **pemeriksaan kualitas**.
4. Halaman yang gagal masuk tangga OCR (§5.3).
5. Simpan hasilnya per blok.

### 5.2 Pemeriksaan kualitas per halaman (DIKALIBRASI, bukan ditebak)

**Langkah A: jenis halaman.** Setiap halaman diberi label `prose | table_heavy | formula_heavy | references | front_matter | figure | blank`. Labelnya diambil dari layout parser atau aturan sederhana, misalnya kepadatan angka dan simbol atau pola sitasi.

**Langkah B: sinyal kualitas.**

| Sinyal | Berlaku untuk | Catatan |
|---|---|---|
| Jumlah karakter | semua kecuali `blank`/`figure` | |
| Rasio kata dikenal (kamus Inggris + kosakata korpus + daftar istilah latin/medis) | hanya `prose` | Halaman rumus, tabel, dan daftar pustaka **tidak** dinilai dengan sinyal ini |
| Rasio karakter rusak | semua | |
| Perplexity model bahasa kecil (opsional) | `prose` | Pendeteksi teks acak yang lebih peka daripada kamus |
| Confidence OCR | **hanya dibandingkan di dalam mesin yang sama** | Skala confidence tiap mesin berbeda |

**Langkah C: kalibrasi.**
- Siapkan **±150 halaman** sampel bertingkat (per jenis halaman × digital/scan). Pengguna atau Claude memberi label `bagus/jelek` dari gambar halaman; Claude memberi usulan, pengguna mengonfirmasi.
- Bagi menjadi 100 halaman kalibrasi dan 50 halaman uji.
- Ambang dipilih pada set kalibrasi (kurva ROC). Targetnya **recall halaman jelek ≥ 95%**, karena lebih baik mengirim halaman bagus ke OCR yang tidak perlu daripada membiarkan halaman sampah lolos.
- Ambang dilaporkan pada 50 halaman uji, terpisah dari set kalibrasi.
- Nilai awal di v0.1 (200 karakter, 85%, 2%, 0.85) hanya dipakai sebagai **titik awal pencarian**.

Hasil OCR diperiksa lagi dengan aturan yang sama. Halaman yang tetap jelek setelah semua mesin dicoba diberi `quality_flag`.

### 5.3 Tangga OCR (urutan ditentukan uji §10.2)
Urutan awal: **PaddleOCR-VL 1.6**, lalu **MinerU 2.5-Pro** untuk halaman tabel atau rumus, lalu **Chandra OCR 2** terkuantisasi, lalu **Tesseract `eng`** lewat OCRmyPDF.
Penantang lain ada di katalog §17.1. Hanya satu model OCR di GPU pada satu waktu.

### 5.4 Format `<sha256>.jsonl`
```json
{"doc": "<sha256>", "pdf_page": 37, "printed_page": "215", "printed_page_conf": 0.93,
 "page_type": "prose", "block": 4,
 "type": "paragraph|heading|table|formula|figure_caption|reference|footnote",
 "section_path": ["3 Methods", "3.2 Sampling"], "text": "...",
 "bbox": [0, 0, 0, 0], "engine": "paddleocr-vl", "engine_version": "1.6",
 "quality": {"chars": 1840, "dict_ratio": 0.97, "garbage": 0.003, "ocr_conf": 0.95},
 "lang": "en"}
```
- **`printed_page`** dideteksi dari header/footer, lalu diperiksa apakah urutannya konsisten antarhalaman (harus naik monoton dengan selisih yang wajar). Kalau `printed_page_conf` < 0.8, sitasi memakai `pdf_page` dan hal itu disebutkan.

### 5.5 `<sha256>.meta.json`
Isinya: judul, penulis, tahun, DOI, jenis, jumlah halaman, nama file, `duplicate_group`, dan ringkasan kualitas. Lookup ke OpenAlex opsional dan mati secara bawaan.
- **Retraction/ralat:** kalau lookup DOI dinyalakan, status retraction diperiksa (OpenAlex/Crossref) dan ditandai `retracted: true` di setiap hasil. Kalau lookup mati, kolom ini bernilai `unknown`.
- **Hash teks per halaman** + `extract_version`. Sitasi menyimpan hash halaman itu. Kalau ekstraksi diperbarui dan teksnya berubah, sitasi lama ditandai "perlu dicek ulang".

### 5.6 Kartu dokumen (BARU)
| Tingkat | Untuk | Isi | Pembuat |
|---|---|---|---|
| **Sederhana** | **semua dokumen**, saat indexing | judul, penulis, tahun, jenis, desain studi (bila terdeteksi), kata kunci, bahasa, kualitas OCR | aturan + model lokal kecil; diberi tanda `confidence: low` |
| **Lengkap** | dokumen yang dipakai dalam riset, dibuat bertahap | pertanyaan riset, desain, sampel, temuan utama, keterbatasan | Claude |

- **Setiap isian kartu lengkap wajib menyertakan kutipan persis + halaman**, lalu dicocokkan otomatis dengan teks halaman itu. Kalau kutipannya tidak ditemukan, isian ditolak.
- Kartu adalah **penunjuk, bukan bukti**. Klaim dalam naskah tetap harus dikutip dari halaman aslinya (`CLAUDE.md` aturan 2).
- `source_spread` memakai kartu sederhana, sehingga sebaran dihitung terhadap **seluruh koleksi**, bukan hanya dokumen yang sudah dibaca.
- Kartu lengkap mengirim teks ke Anthropic. Untuk folder `private`, kartu lengkap tidak dibuat.

## 6. Lapis 2: indeks

### 6.1 Chunking
- Berdasarkan struktur: per subbab. Paragraf digabung sampai **±500 token tokenizer model embedding yang dipakai** (rentang 300–800), dengan overlap ±15% dan tanpa memotong kalimat. *Asal angka: nilai umum di literatur chunking; diuji sebagai nilai awal (lihat §17.3, arXiv 2602.16974 menyatakan chunking optimal bergantung tugas).*
- **Tabel** jadi satu chunk utuh. **Rumus** ikut paragraf di sekitarnya.
- **Daftar pustaka dan catatan kaki** ditandai dan tidak ikut pencarian bawaan.
- **Header konteks**: `"<Judul> (<Tahun>) > <Bab> > <Subbab>"`.

### 6.2 Embedding dan pencarian (konfigurasi awal; pemenang ditentukan §10.3)
| Bagian | Awal | Penantang (lihat §17) |
|---|---|---|
| Dense | Qwen3-Embedding-8B / 4B | Jasper-600M, bge-m3, ColBERT (late interaction) |
| Leksikal | BM25 (full-text LanceDB) | SPLADE-v3 / OpenSearch neural sparse |
| Fusi | RRF, k=60 (*Cormack dkk. 2009*) | fusi berbobot, diuji di set dev |
| Penyimpanan | LanceDB per folder | |

### 6.3 `manifest.json`
Isinya:
- Model embedding: nama, **revisi commit HF**, dimensi, kuantisasi.
- **String instruksi query** (Qwen3-Embedding memakai instruksi tugas di depan query, dan instruksi ini dibekukan).
- Tokenizer, versi chunker, parameter, versi parser, versi pustaka, seed, dan jumlah dokumen/chunk.

**Query ditolak** kalau ada yang tidak cocok.

## 7. Alur pencarian

```
Claude (pertanyaan riset → diperjelas → diterjemahkan ke Inggris)
  → [1] embedding query → vektor
  → [2] dense top-100 + leksikal top-100 → RRF → 100 kandidat
  → [3] reranker → urutkan ulang
  → [4] top-k (bawaan 10) + konteks + source_spread
  → Claude membaca, menilai, mencatat chunk yang dipakai/dibuang, lalu menjalankan search_counter
```
- Batas konteks: setiap hasil maksimal ±300 kata, dan satu panggilan maksimal ±4.000 kata.

### 7.1 Anggaran latensi dan memori (BARU)
- **Target: ≤ 20 detik per panggilan `search`** setelah model dimuat pertama kali. Satu pertanyaan riset bisa butuh 5–10 panggilan.
- Diukur di langkah 0 (§14) untuk tiga skema:
  - **S1:** server MCP tetap hidup, embedding tetap di GPU, reranker dimuat bergantian.
  - **S2:** embedding query di CPU, reranker tetap di GPU.
  - **S3:** model 4B untuk keduanya, tetap di GPU.
- Skema yang dipilih adalah yang paling akurat di §10.3 **dan** memenuhi target latensi. Kalau 8B melewati batas, mundur ke 4B dan selisih akurasinya dilaporkan.
- RAM: 8B 8-bit tidak disimpan di RAM bersama model lain. Jika RAM kurang, pakai 4-bit atau 4B.

## 8. Tool MCP

| Tool | Parameter | Output |
|---|---|---|
| `index_folder` | `folder`, `resume=true` | Status antrian, estimasi, ringkasan kualitas |
| `index_status` | `folder` | Progres, file gagal, halaman jelek |
| `search` | `folder`, `query`, `k=10`, `filters` | Hasil + `source_spread` + `params_used` |
| `search_counter` | `folder`, `claim`, `k=10` | Hasil yang diarahkan ke keterbatasan, kritik, dan hasil negatif; **dokumen sumber klaim tidak diutamakan** |
| `get_context` | `chunk_id`, `window=1` | Teks sekitar chunk |
| `get_page` | `doc`, `pdf_page` | Teks + gambar halaman |
| `list_documents` | `folder`, filter (tahun, jenis, desain, subfolder) | Kartu sederhana: judul, tahun, jenis, kualitas, duplikat |
| `get_outline` | `doc` | Daftar isi (bab/subbab + halaman). Kalau tidak terdeteksi (sering terjadi pada buku scan), mengembalikan rentang halaman dan **menyatakan bahwa outline tidak tersedia** |
| `read_section` | `doc`, `section` atau `pages` | Satu bagian utuh (dibatasi ±4.000 kata) |
| `search_exact` | `folder`, `phrase`/regex | Kecocokan persis (istilah, singkatan, angka, frasa seperti "no significant") + jumlah halaman berkualitas rendah yang tidak bisa diandalkan |
| `get_card` | `doc` | Kartu sederhana/lengkap beserta kutipan dan halamannya |
| `verify_citation` | `doc`, `page`, `quote` | Apakah kutipan itu benar ada di halaman tersebut (dipakai sebelum jawaban akhir) |
| `log_usage` | `query_id`, `used[]`, `discarded[]`, `reason` | Catatan chunk yang dipakai/dibuang Claude (audit bias pembaca) |
| `eval_run` | `folder`, `config`, `split=dev` | Metrik §10; `split=test` hanya bisa dijalankan sekali per konfigurasi final |

### 8.1 Bentuk satu hasil
```json
{"rank": 1, "chunk_id": "...", "doc": "Smith 2021 - Title.pdf", "doi": "...",
 "printed_page": "215", "pdf_page": 37, "page_cite_basis": "printed|pdf",
 "section": "3.2 Sampling", "text": "...",
 "scores": {"dense": 0.71, "lexical": 12.3, "rerank": 0.92},
 "quality_flag": null, "duplicate_group": null}
```
### 8.2 `source_spread`
Isinya: jumlah dokumen unik, jumlah hasil per dokumen, rentang tahun, dan indeks konsentrasi (Herfindahl). Peringatan muncul kalau lebih dari 50% hasil berasal dari satu dokumen atau grup. *Ambang 50% adalah nilai awal untuk peringatan saja; ditinjau setelah ada data log.*

## 9. Penegakan CLAUDE.md oleh alat
| Aturan | Penegakan |
|---|---|
| 2: konteks dan sumber | `get_context`, `get_page`; halaman wajib; `page_cite_basis` |
| 3: bukti berlawanan dan sebaran | `search_counter` (tidak mengutamakan dokumen asal klaim); `source_spread` |
| 4: koleksi bukan dunia | Output menyebut "hanya dari koleksi folder X" |
| 5: transparansi | `params_used`, `quality_flag`, `log_usage`, `logs/queries.jsonl` |
| 6: keputusan besar | Di luar alat (`methodology-review` / `llm-council`) |
| 7: verifikasi | `verify_citation` untuk setiap kutipan sebelum jawaban akhir |

### 9.1 Prosedur pencarian bukti berlawanan (pengganti logika `search_counter`)
Komputer tidak bisa menilai mana temuan yang "berlawanan". Karena itu `search_counter` hanya berarti **pencarian dengan istilah berlawanan**, dan Claude wajib menjalankan prosedur ini:
1. `search` dengan klaim yang dinegasikan.
2. `search_exact` untuk frasa seperti "no significant", "did not", "failed to", "limitation", "contrary to", dan "in contrast".
3. `list_documents` dengan filter desain/tahun untuk studi yang belum muncul di hasil.
4. Bandingkan kartu, lalu laporkan studi yang mendukung, yang membantah, yang netral, dan **berapa persen koleksi yang tidak bisa dicari dengan baik** karena OCR jelek.

### 9.2 Audit bias pembaca
- `log_usage` mencatat setiap pencarian: apa yang muncul, apa yang dikutip, dan apa yang dibuang.
- Ringkasan per sesi menunjukkan:
  - persentase kutipan yang **berasal dari hasil pencarian** dibandingkan dari pengetahuan model;
  - satu dokumen yang menyumbang >40% kutipan (diberi peringatan; ambang awal);
  - kedalaman baca per dokumen.
- **Bias era akibat OCR:** laporan kualitas per dekade terbit. Kalau studi lama lebih banyak yang jelek OCR-nya, hal itu disebutkan sebagai keterbatasan.

## 10. Evaluasi (ditulis ulang)

### 10.1 Soal uji
- **Target 120 soal** per folder uji, dengan **minimal 2 folder dari bidang berbeda** sebelum konfigurasi dikunci untuk semua folder.
- **Sumber soal** (dilaporkan terpisah):
  - **≥ 50% dari pengguna**: pertanyaan riset nyata yang ditulis **sebelum melihat hasil pencarian**. Claude boleh membantu merumuskan, tapi tidak dari teks dokumen.
  - **≤ 50% dari Claude**: dibuat dari dokumen lalu **diparafrasekan**, supaya kata-katanya tidak sama dengan teks jawaban.
- **Jenis soal** (ditandai per soal): fakta, konsep, perbandingan antarpaper, istilah/singkatan, isi tabel, halaman scan, dan penalaran. Hasil dilaporkan per jenis.
- **Pembagian**: 70% **dev** untuk memilih dan 30% **test** yang dikunci. Hash file test dicatat. Test dibuka **sekali**, untuk konfigurasi final saja.
- **Ukuran sampel**: dihitung dengan `statistical-power` sebelum soal dibuat. Targetnya bisa mendeteksi selisih nDCG@10 ≥ 3 poin dengan uji berpasangan, power 0.8. Kalau 120 soal tidak cukup, jumlahnya dinaikkan.
- Soal ditandai dengan versi korpus. Kalau folder bertambah lebih dari 20%, soal dan qrels ditinjau ulang.

### 10.2 Penilaian relevansi (pooling)
- Untuk setiap soal, gabungkan **top-20 dari semua konfigurasi yang diuji** (ditambah halaman yang sudah diketahui relevan).
- Pengguna menilai setiap item dengan skala **0 (tidak relevan) / 1 (sebagian) / 2 (menjawab)** **tanpa melihat konfigurasi asalnya** (urutan diacak).
- Aturan pencocokan: chunk relevan kalau mendapat nilai ≥1. Item yang belum dinilai **dilaporkan jumlahnya** (`judged@10`) dan tidak diam-diam dianggap salah.
- Claude boleh memberi penilaian awal untuk mempercepat. Kesepakatan Claude dan pengguna diukur dengan Cohen's κ pada 20% sampel; kalau κ < 0.6, semua dinilai pengguna.

### 10.3 Uji OCR
- **Sampel bertingkat**: ±10 halaman per jenis (`prose` digital, `prose` scan bagus, scan buruk, dua kolom, tabel, rumus, catatan kaki), total ±60–70 halaman, diambil acak dari tiap jenis.
- **Kunci jawaban tanpa jangkar**: diketik dari gambar halaman, atau dikoreksi dari gabungan keluaran beberapa mesin yang **ditampilkan tanpa nama mesin**.
- Metrik:
  - CER/WER per jenis halaman, keutuhan tabel (TEDS bila memungkinkan), dan urutan baca.
  - **Dampak hilir**: recall@10 pada soal yang jawabannya ada di halaman scan, dibandingkan antarmesin.
- Hasilnya menentukan urutan tangga §5.3 dan aturan pengalihan per jenis halaman.

### 10.4 Uji pencarian: bertahap, bukan kombinasi penuh
Semua pada set **dev**. Metrik: recall@100, recall@10, MRR@10, nDCG@10, `judged@10`.

| Tahap | Yang diadu | Yang dikunci |
|---|---|---|
| 1 | Dense: Qwen3-8B (8/4-bit), Qwen3-4B, Jasper-600M, bge-m3, ColBERT (§17.2) | tanpa reranker, chunking awal |
| 2 | Leksikal: BM25 vs SPLADE; mode: dense / leksikal / hibrida | pemenang tahap 1 |
| 3 | Reranker: tanpa, Qwen3-8B-4bit, Qwen3-4B, mxbai-large-v2, jina-reranker-v3, bge-reranker-v2-m3 | pemenang tahap 2 |
| 4 | Fitur chunk: header konteks, contextual retrieval, late chunking, ukuran chunk 300/500/800 | pemenang tahap 3 |
| 5 | Query: asli vs ditulis ulang Claude vs multi-query/HyDE | pemenang tahap 4 |

- **Uji statistik**: **bootstrap berpasangan pada selisih** (10.000 resampel, per soal) atau uji permutasi. Batas keputusan ditetapkan **sebelum** uji: **selisih praktis minimal nDCG@10 = 2 poin**.
- **Aturan keputusan**: penantang menggantikan bawaan hanya kalau batas bawah CI 95% selisihnya > 0 **dan** selisihnya ≥ 2 poin. Kalau tidak, pakai yang lebih ringan.
- **Koreksi perbandingan berganda**: di tiap tahap dipakai Holm-Bonferroni.
- **Konfirmasi**: konfigurasi final dijalankan **sekali** pada set test dan dilaporkan apa adanya, termasuk kalau skornya turun.
- Semua run dicatat dengan `experiment-log`: run_id, revisi model, seed, versi pustaka, dan hash data. Hasil GPU diulang 2× untuk mengecek nondeterminisme.

### 10.5 Analisis kegagalan per tahap
Setiap soal yang gagal diberi label penyebab: **OCR/ekstraksi**, **chunking**, **retrieval** (tidak masuk top-100), **ranking** (masuk top-100 tapi tidak masuk top-10), atau **label** (sebenarnya relevan tapi belum dinilai).

### 10.6 Uji ujung ke ujung (BARU)
- 20 soal dari set test dijawab Claude memakai RAG.
- Setiap klaim dalam jawaban dicek dengan `citation-verification`: apakah halaman yang dikutip memang mendukung klaimnya.
- `log_usage` diaudit untuk melihat apakah chunk yang dibuang Claude justru yang relevan (bias pembaca).
- Pembanding: jawaban **tanpa RAG** untuk soal yang sama (`CLAUDE.md` aturan 6).

### 10.7 Uji anti-bias
- 15 klaim dengan bukti berlawanan yang **sudah diketahui ada** di koleksi, ditentukan pengguna sebelum uji.
- Metrik:
  - **Recall bukti berlawanan@10** dari `search_counter`.
  - Persentase hasil yang berasal dari **dokumen selain sumber klaim**.
  - Pembanding: `search` biasa dengan klaim yang sama.
- Pengujian peringatan sebaran: buat kasus buatan di mana satu dokumen mendominasi, lalu pastikan peringatannya muncul.

## 11. Privasi, hak cipta, keamanan
- Indexing dan model berjalan lokal. **Chunk yang dibaca Claude terkirim ke Anthropic.** Folder dengan `private: true` hanya mengembalikan metadata.
- Lookup OpenAlex/Crossref mati secara bawaan. Kalau dinyalakan, yang dikirim hanya DOI atau judul.
- Pustaka berhak cipta untuk riset pribadi. Indeks tidak dibagikan.
- Model hanya dari Hugging Face resmi. Revisi dan lisensinya dicatat. **Lisensi setiap model dicek sebelum dipakai** (TeleOCR dan OvisOCR2 belum dicek).

## 12. Tahap 2 (bersyarat)
- **Distillation reranker**: hanya kalau §10.5 menunjukkan ranking sebagai titik lemah **dan** reranker 8B melewati anggaran §7.1. Dipakai hanya kalau selisih nDCG@10 terhadap guru ≤ 2 poin pada set test.
- **Fine-tuning embedding dari pertanyaan nyata** (`logs/queries.jsonl` + `log_usage`): lebih disukai daripada query sintetis.
- **Pencarian visual** (ColQwen / ColPali): hanya kalau banyak kegagalan di halaman scan, grafik, atau tabel.
- **Struktur tingkat tinggi** (RAPTOR / GraphRAG, §17.4): hanya kalau soal jenis "perbandingan antarpaper" atau "penalaran" jelas lebih lemah.
- **Pencarian lintas folder**: setelah embedding seragam.
- Encoder query asimetris: **dibatalkan**.

## 13. Operasional
| Perintah | Fungsi |
|---|---|
| `rag index <folder>` | Indexing; bisa dilanjutkan |
| `rag status <folder>` | Progres dan masalah |
| `rag rebuild-index <folder>` | Bangun ulang lapis 2 tanpa OCR ulang |
| `rag eval <folder> --config X --split dev` | Uji |
| `rag bench-latency` | Ukur §7.1 |
| `rag doctor` | Cek GPU, driver, model, Tesseract, disk |
| `rag backup <folder>` | Salin `extract/` + `manifest.json` + `eval/` |

- Log ditulis dalam bahasa sederhana. Folder di cloud sync ditolak.

### 13.1 Pemantauan oleh pengguna (BARU, wajib)
- Indexing berjalan di **jendela terminal yang terlihat**. Jendela itu menampilkan file dan halaman yang sedang diproses, mesin OCR yang dipakai, peringatan halaman jelek, jumlah file selesai/total, dan **perkiraan sisa waktu**.
- Semua kejadian juga ditulis ke `logs/index.log` (bisa dibuka langsung di VS Code).
- `rag status` dapat diketik pengguna kapan saja.
- `_quality_report.csv` bisa dibuka di Excel.
- Pemakaian GPU terlihat di Task Manager.
- Opsional setelah versi 1: dashboard `localhost` dengan grafik progres dan contoh halaman OCR.

### 13.2 Pemakaian berulang (BARU)
- **Tambah paper:** `rag index` lagi. Hanya file baru (hash baru) yang diproses. File yang dihapus dari folder ikut dihapus dari indeks.
- **Ganti model atau pengaturan:** `rag rebuild-index`, tanpa OCR ulang.
- **Folder atau topik baru:** alat yang sama, indeks sendiri per folder.
- **Uji ulang:** soal uji disimpan dan dijalankan lagi setiap ada perubahan, sebagai uji regresi.
- **Hasil bisa diulang:** dengan bahan dan manifest yang sama, hasilnya sama.
- **Backup berkala** lapis 1 ke media eksternal (`rag backup`).

### 13.3 Lingkungan dan editor
- Satu lingkungan Python khusus (`uv`) untuk alat, di luar Python sistem. Untuk versi 1: **satu** OCR VLM + Tesseract dalam satu lingkungan.
- Server MCP dinyalakan otomatis oleh Claude Code. Bisa dipakai dari terminal, VS Code (ekstensi Claude Code), atau editor turunan VS Code.
- `rag doctor`: cek GPU/driver/model/disk **dan** menyarankan perbaikan untuk kerusakan yang umum.
- `rag smoke`: index 5 PDF contoh + 3 pertanyaan, dijalankan setelah setiap perubahan.
- Ada tes otomatis untuk: deteksi kualitas, format JSONL, manifest mismatch, resume, kunci set test, dan alur end-to-end pada 3 PDF contoh.

## 14. Rencana pengerjaan
0. `rag doctor`, instalasi, **uji instalasi OCR VLM di Windows**, dan **ukur latensi §7.1**.
1. Kalibrasi quality gate (§5.2) dan uji OCR (§10.3).
2. Lapis 1 + indexing folder uji pertama.
3. Lapis 2 + `search`/`get_context` + 30 soal uji asap.
4. Hitung power, susun 120 soal (dev/test), pooling + penilaian, lalu uji bertahap §10.4 dan analisis kegagalan.
5. Ulangi langkah 4 secara ringkas di folder kedua (bidang lain).
6. Kunci konfigurasi, buka set test sekali, uji ujung ke ujung dan anti-bias, lalu tambahkan aturan pemakaian ke `CLAUDE.md`.
7. Indexing folder lain secara bertahap.

### 14.1 Urutan baru (menggantikan §14 untuk versi 1; §14 berlaku setelah versi 1 lulus)
0. Pengguna menyalin pustaka ke `C:\Pustaka\` (di luar cloud sync), lalu `rag doctor` dan instalasi.
1. **Ekstraksi saja** pada ±50 PDF campuran (digital + scan). **Pengguna dan Claude memeriksa 20 halaman dengan mata**: teks OCR dan nomor halaman.
2. Indeks + alat versi 1 (§18) + `rag smoke`.
3. **30 pertanyaan riset nyata** dari pengguna. Recall@50 dinilai manual, dan kutipannya dicek dengan `verify_citation`.
4. Lulus versi 1 → indexing seluruh pustaka bertahap (semalaman) → protokol §10 lengkap saat ada dua konfigurasi yang perlu dibandingkan.

## 15. Risiko utama
| Risiko | Dampak | Mitigasi |
|---|---|---|
| OCR VLM gagal diinstal di Windows | Kualitas OCR turun | WSL; cadangan Tesseract |
| Kuantisasi 8B menurunkan kualitas | Akurasi turun | Diukur di tahap 1; mundur ke 4B |
| Ambang kualitas salah | Halaman sampah lolos | Kalibrasi ROC + set uji terpisah; `quality_flag` |
| Soal uji melingkar | Skor palsu | ≥50% soal dari pengguna; parafrase; dilaporkan per sumber |
| Label tidak lengkap | Metrik bias | Pooling, penilaian buta, `judged@10` |
| Overfitting pada pemilihan konfigurasi | Skor terlalu optimis | Dev/test terkunci; uji bertahap; Holm |
| Hasil tidak berlaku di folder lain | Pilihan salah untuk topik lain | ≥2 folder bidang berbeda |
| Query terlalu lambat | RAG tidak dipakai | Anggaran §7.1, mundur ke 4B |
| Bias pembaca akhir (Claude) | Bukti dibuang diam-diam | `log_usage` + audit §10.6 |
| Indexing terputus | Waktu terbuang | Antrian bisa dilanjutkan; cache per hash |
| Lapis 1 hilang | OCR ulang berhari-hari | `rag backup` ke media eksternal; tolak cloud sync |
| Kartu bertahap membuat sebaran bias | Bukti berlawanan tak terlihat | Kartu sederhana untuk semua dokumen; sebaran terhadap seluruh koleksi |
| Kartu salah dianggap fakta | Kesalahan menyebar diam-diam | Kutipan persis + halaman wajib, dicocokkan otomatis; kartu hanya penunjuk |
| Proyek terlalu rumit lalu ditinggalkan | Seluruh usaha sia-sia | Versi 1 kecil; `rag doctor`, `rag smoke`, satu lingkungan |
| GPU kehabisan memori (OOM) | Server mati | Cadangan reranker di CPU sejak awal |
| Paper yang ditarik ikut dikutip | Klaim salah | Penanda retraction (kalau lookup DOI dinyalakan) |
| Teks halaman berubah setelah ekstraksi ulang | Sitasi lama salah | Hash teks per halaman + `extract_version` |
| Bias era akibat OCR | Studi lama kurang terwakili | Laporan kualitas per dekade |

## 16. Pertanyaan terbuka untuk pengguna
1. **Path pustaka** (disalin ke luar cloud sync, misalnya `C:\Pustaka\`) dan ±50 PDF untuk uji versi 1. Folder kedua (bidang lain) dibutuhkan nanti, untuk §10.
2. ~~Ada folder yang harus `private`?~~ **Diputuskan 2026-11-18** (council + pengguna): `PUSTAKA MATEMATIKA-AI` tidak private. Folder baru **tanpa bawaan**: saat didaftarkan wajib ditanya. Pedoman private: naskah belum terbit, draf orang lain, skripsi yang sedang diuji, bahan berlisensi terbatas. Paper terbit (termasuk medis) tidak perlu private. Data pasien asli tidak boleh masuk pustaka. Catatan: mode private tetap mengirim nama file, judul, dan kata pencarian.
3. ~~Ruang disk~~: **terjawab** (ruang disk cukup).
4. Untuk versi 1: bersedia menulis **30 pertanyaan riset nyata** dan memeriksa 20 halaman OCR? (Pengguna **tidak perlu hafal isi folder**; daftar dan kartu dokumen dibuatkan.) Beban §10 lengkap menyusul nanti.
5. ~~Lookup DOI ke OpenAlex?~~ **Diputuskan 2026-11-18**: nyala **hanya lewat DOI** (uji 20 dok: DOI 5/5 benar, judul top-1 0/15 benar; `eval/openalex_trial/REPORT.md`). Lookup judul mati. Dokumen tanpa DOI: retraction = "belum dicek" (bukan "tidak ditarik"). Hasil dicache lokal; tetap pakai `citation-verification` sebelum naskah.

---

## 17. Katalog opsi (bahan diskusi; semua belum diuji pada data pengguna)

Status: **U** = sudah masuk uji §10, **C** = cadangan (masuk uji kalau analisis kegagalan menunjukkannya perlu), **B** = untuk belajar/dipantau.

### 17.1 Parsing dan OCR
| Opsi | Status | Catatan |
|---|---|---|
| PyMuPDF (lapisan teks) | U | Untuk PDF digital, cepat |
| PaddleOCR-VL 1.6 | U | OmniDocBench v1.6: 96.34 |
| MinerU 2.5-Pro | U | 95.75; kuat di tabel dan rumus |
| GLM-OCR | U | 95.22 (dilaporkan pihak ketiga) |
| Chandra OCR 2 | U | Scan rusak, tulisan tangan |
| TeleOCR / OvisOCR2 | C | Skor tinggi, lisensi belum dicek |
| dots.ocr, DeepSeek-OCR | C | Disebut bagus untuk layout; belum ada angka OmniDocBench yang terverifikasi |
| olmOCR, Marker/Surya, Docling, GOT-OCR 2.0 | C | Alternatif yang mapan |
| Tesseract via OCRmyPDF | U | Cadangan pasti jalan |
| OXR | B | Skor tertinggi (97.03), tapi lisensinya berbayar |

### 17.2 Retrieval pertama
| Opsi | Status | Catatan |
|---|---|---|
| Qwen3-Embedding-8B / 4B | U | Unggulan MTEB |
| Jasper-600M | U | MTEB English v2 74.75, ringan |
| bge-m3 | U | Pembanding standar |
| **ColBERT / late interaction** (PyLate; GTE-ModernColBERT, ColBERT-Zero) | U | Laporan awal: lebih tahan pada kueri penalaran (BRIGHT ±19.6 vs 12.3 dense satu backbone) dan domain khusus; bukti sebagian dari pembuat model |
| BM25 | U | Istilah, singkatan, nama metode |
| **SPLADE-v3 / OpenSearch neural sparse** | U | Leksikal "pintar"; SPLADE-v3 mengalahkan BM25 di 41/44 set kueri (makalahnya) |
| Pencarian visual ColQwen/ColPali | C | Halaman grafik/tabel/scan |

### 17.3 Chunking dan pengayaan chunk
| Opsi | Status | Catatan |
|---|---|---|
| Struktural ±500 token | U | Awal |
| Ukuran 300 / 800 | U | Tahap 4 |
| Header konteks | U | Murah |
| **Contextual retrieval** (LLM lokal menulis konteks per chunk) | U | Koherensi lebih baik, tapi lebih mahal (arXiv 2504.19754) |
| **Late chunking** | U | Efisien, kadang kurang lengkap (arXiv 2504.19754) |
| Semantic chunking | C | Bukti tidak konsisten; optimal tergantung tugas (arXiv 2602.16974) |
| Proposition / parent-child (cari kecil, kirim besar) | C | Bisa dicoba lewat `get_context` |

### 17.4 Struktur tingkat tinggi
| Opsi | Status | Catatan |
|---|---|---|
| RAPTOR (pohon ringkasan) | C | Untuk pertanyaan tingkat ringkasan atau lintas bab |
| GraphRAG / LightRAG | C | Untuk relasi lintas paper; mahal saat indexing |
| Graph-aware late chunking | B | Biomedis, arXiv 2603.22633 (2026) |
| Grafik sitasi antarpaper (dari daftar pustaka) | C | Membantu `source_spread` per kelompok penulis |

### 17.5 Reranker
| Opsi | Status | Catatan |
|---|---|---|
| Qwen3-Reranker-8B (4-bit) / 4B | U | Unggulan |
| mxbai-rerank-large-v2 | U | Ringan |
| **jina-reranker-v3** (listwise) | U | BEIR 61.94 dilaporkan; cek lisensi |
| bge-reranker-v2-m3 | U | Pembanding standar |
| zerank (versi open-weight) | C | Info terbatas |
| Claude sebagai reranker akhir | U (implisit) | Sudah jadi pembaca akhir; dicatat lewat `log_usage` |

### 17.6 Pengolahan query
| Opsi | Status | Catatan |
|---|---|---|
| Claude memperjelas dan menerjemahkan | U | Bawaan |
| Multi-query (3–5 varian, digabung RRF) | U | Tahap 5 |
| HyDE (jawaban hipotetis jadi query) | U | Tahap 5; risiko bias jawaban, jadi diuji |
| Dekomposisi pertanyaan majemuk | C | Untuk soal penalaran |

### 17.7 Alat evaluasi
| Opsi | Status | Catatan |
|---|---|---|
| `ranx` / `ir_measures` | U | Metrik IR standar + uji statistik berpasangan |
| Pooling gaya TREC | U | §10.2 |
| RAGAS / LLM-judge | C | Hanya sebagai pelengkap; tidak menggantikan penilaian pengguna |
| `citation-verification` | U | §10.6 |

### 17.8 Sistem siap pakai (pembanding, bukan untuk dipakai langsung)
PaperQA2, kotaemon, RAGFlow, LightRAG, khoj, R2R. Salah satunya (PaperQA2, karena dirancang untuk paper ilmiah) bisa dijadikan **baseline eksternal** di folder uji, sehingga terlihat apakah sistem buatan sendiri benar-benar lebih baik.

### Sumber katalog
- OCR: [Unstract 2026](https://unstract.com/blog/best-opensource-ocr-tools/), [Roboflow](https://blog.roboflow.com/best-open-source-ocr-models/), [pasqualepillitteri 2026](https://pasqualepillitteri.it/en/news/25568/best-local-ocr-pc-server-2026)
- Late interaction: [LightOn ColBERT-Zero](https://lighton.ai/lighton-blogs/day-zero-of-multi-vector-retrieval), [TurkColBERT arXiv 2511.16528](https://arxiv.org/abs/2511.16528v1), [Hamel: late interaction](https://hamel.dev/notes/llm/rag/p4_late_interaction.html)
- Chunking: [arXiv 2504.19754](https://arxiv.org/pdf/2504.19754v1.pdf), [arXiv 2602.16974](https://arxiv.org/pdf/2602.16974), [arXiv 2603.22633](https://arxiv.org/html/2603.22633), [Frontiers RAPTOR 2026](https://www.frontiersin.org/journals/computer-science/articles/10.3389/fcomp.2025.1710121/full)
- Reranker: [presenc.ai 2026](https://presenc.ai/research/best-open-weight-reranker-models-2026), [MachineLearningMastery](https://machinelearningmastery.com/top-5-reranking-models-to-improve-rag-results/) (angka antarsumber tidak konsisten; verifikasi di model card)
- Sparse: [SPLADE-v3 arXiv 2403.06789](https://web3.arxiv.org/pdf/2403.06789), [ringkasan sparse vs dense](https://bigdataboutique.com/blog/sparse-vs-dense-vectors-how-lexical-and-semantic-search-actually-work)

---

## 18. Versi 1 (MVP), sengaja kecil

| Bagian | Isi versi 1 | Ditunda |
|---|---|---|
| Ekstraksi | PyMuPDF + **satu** OCR VLM (PaddleOCR-VL 1.6, kalau gagal diinstal: MinerU) + Tesseract | Mesin OCR lain di katalog §17.1 |
| Quality gate | Nilai awal §5.2 + pemeriksaan 20 halaman dengan mata | Kalibrasi ROC penuh (§5.2 langkah C) |
| Kartu | Kartu sederhana untuk semua dokumen | Kartu lengkap (bertahap, setelah pencarian lulus) |
| Indeks | Qwen3-Embedding-4B + BM25 + RRF | 8B, ColBERT, SPLADE, contextual retrieval |
| Reranker | Satu reranker 4B (cadangan CPU) | Reranker lain |
| Alat MCP | `list_documents`, `get_outline`, `read_section`, `search`, `search_exact`, `get_page`, `get_card`, `verify_citation`, `log_usage`; `source_spread` di setiap hasil | Dashboard, `eval_run` penuh |
| Proses | Satu indexer (jendela terlihat) + satu server MCP | |
| Evaluasi | 30 pertanyaan nyata, recall@50 manual, cek kutipan | Protokol §10 lengkap |
| Operasional | `rag index/status/rebuild-index/backup/doctor/smoke` | |

**Definisi selesai versi 1:** Claude menjawab 30 pertanyaan riset nyata pengguna dari satu folder. Setiap kutipan file + halaman benar (lolos `verify_citation`), dan `source_spread` tampil di setiap hasil.
