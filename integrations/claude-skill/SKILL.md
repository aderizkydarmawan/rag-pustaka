---
name: rag-pustaka
description: Cari dan kutip isi pustaka PDF lokal milik pengguna (paper dan buku per folder topik) lewat server MCP `rag-pustaka`, dengan aturan anti-bias. Gunakan saat pengguna bertanya tentang isi pustakanya, minta "cari di pustaka saya", butuh kutipan file + halaman dari koleksinya, menulis tinjauan pustaka/tugas/paper dari bahan yang ia kumpulkan, atau saat jawaban riset perlu dibumikan ke koleksinya. Berlaku di folder mana pun.
---

# RAG pustaka lokal

Server MCP `rag-pustaka` (scope user) mencari pustaka yang sudah diindeks (`<folder>/.rag/`). RAG adalah **pembawa bahan, bukan penentu arah riset**.

## Memilih pustaka
- Tanpa parameter `library`, server memakai folder ber-`.rag` di folder kerja sekarang (atau induknya); kalau tidak ada dan hanya satu pustaka terdaftar, itu yang dipakai.
- `list_libraries` menunjukkan semua pustaka terdaftar. Sebutkan `library` kalau ada lebih dari satu.

## Alur wajib
1. **Arah riset dikunci dulu.** Jangan merumuskan pertanyaan riset dari hasil pencarian.
2. **Tulis query dalam bahasa Inggris** dengan istilah teknis; pecah pertanyaan besar menjadi beberapa pencarian. Cari 5–30 kali per tugas bila perlu.
3. **Baca konteks** (`get_context` / `read_section`) sebelum sebuah potongan dipakai untuk klaim.
4. **Bukti berlawanan** untuk setiap klaim penting — prosedur, bukan satu tombol:
   - `search` dengan klaim yang dinegasikan / "limitation of X" / "criticism of X";
   - `search_exact` frasa "no significant", "did not", "failed to", "contrary to", "limitation";
   - `list_documents` untuk melihat dokumen di koleksi yang belum muncul.
5. **Baca `source_spread` dan `coverage`** di setiap hasil. Kalau ada peringatan dominasi atau banyak dokumen belum bisa dicari (menunggu OCR), sebutkan sebagai keterbatasan.
6. **Kutip file + halaman.** Pakai `printed_page` kalau `page_cite_basis = printed`; kalau tidak, sebut "hlm. PDF n".
7. **`verify_citation`** untuk setiap kutipan langsung sebelum jawaban akhir. `not_found` → jangan disajikan sebagai kutipan.
8. **`log_usage`** setelah memilih potongan: mana yang dipakai dan mana yang dibuang beserta alasannya.
9. **Rumus hasil OCR** bisa salah baca simbol; cek dengan `get_page(..., image=True)` sebelum mengutip persamaan.

## Pelabelan jawaban
Bedakan **dari RAG [file, hlm]**, **dari pencarian luar**, dan **dari pengetahuan model**, dengan tingkat keyakinan (terbukti / ada indikasi / dugaan). Koleksi bukan dunia: untuk klaim penting cek juga di luar koleksi (`litreview` / `deep-research`). Jawaban medis bukan diagnosis.

## Status alat (versi 1)
- `search` bawaan **hibrida** (BM25 + makna/embedding Qwen3, digabung RRF); `mode="bm25"` untuk kata persis, `mode="dense"` untuk makna saja. `found_by` menunjukkan jalur yang menemukan tiap hasil. Lalu 30 kandidat teratas diurutkan ulang oleh reranker Qwen3 (`scores.rerank` 0–1; `rerank=False` untuk mematikan).
- Pencarian pertama per sesi butuh ±30–45 dtk (memuat 2 model ke GPU, ±6 GB VRAM); berikutnya ±7–10 dtk (±1 dtk dengan `rerank=False`). Skor rerank tinggi = relevan menurut model, BUKAN bukti kuat.
- Semua dokumen sudah di-OCR; halaman ber-`quality_flag` perlu dicek dengan `get_page(..., image=True)`.
- Kartu dokumen masih **sederhana** (judul, tahun ditebak dari nama file).

## Pemeliharaan (untuk pengguna)
- Indeks ulang setelah menambah PDF: `python -m rag.extract "<folder>"` lalu `python -m rag.library build "<folder>"` (dari akar repo).
- Spesifikasi: `docs/SPEC.md`.
