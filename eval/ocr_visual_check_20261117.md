# Cek visual 20 halaman OCR (2026-11-17)

Sampel acak berstrata (seed 42) dari 6.803 halaman OCR (PaddleOCR-VL 1.6). Setiap halaman: gambar (`get_page(image=True)`) dibandingkan dengan teks OCR oleh Claude. Penilaian kualitatif, bukan CER terukur.

Skala: **Baik** = prosa utuh, rumus benar atau hampir benar · **Cukup** = prosa utuh, beberapa simbol/rumus salah · **Buruk** = ada isi hilang, rumus salah sehingga maknanya berubah, atau ada teks karangan · **Gagal** = sebagian besar isi hilang.

| # | Strata | Dokumen, hlm. PDF (cetak) | Nilai | Catatan |
|---|---|---|---|---|
| 1 | prosa scan | Boyce–DiPrima 731 (715) | Cukup | prosa utuh; `sin mπx` → `sin m x`; `n√x e^{-nx²/2}` → `n√2 e^{-n²/2}` |
| 2 | prosa scan | Pathria 38 (27) | Buruk | banyak simbol salah: `σ³`→`σ²`, `V−b`→`V−θ`, `5/3`→`3/3`, `PV^γ`→`PV^V`, `(ΔS)_P`→`(ΔS)μ` |
| 3 | prosa scan | Feller 224 (206) | Cukup | sangat rapi; `P{A_v}`→`P{A_1}`, `γ^r`→`γ r`, subskrip `S_{n_{r+1}}`→`S_{n+1}` |
| 4 | prosa scan | Vapnik 144 (124) | Baik | satu salah angka: "1960s" → "1900s" |
| 5 | prosa scan | Risken 14 (2) | Baik | `Γ(t)` terbaca `F'(t)`/`I'(t)`; `F_f`→`F_t` |
| 6 | prosa scan | Boyce–DiPrima 413 (397) | Buruk | prosa utuh, tapi `e^{r t}`→`e^{x t}`, pers. (30) diganti salinan (25), dan **4 persamaan karangan** di akhir halaman yang tidak ada di gambar |
| 7 | prosa scan | Risken 157 (145) | Cukup | `∫` terbaca `δ`; "eigenfunctions" → "eigenvalues" |
| 8 | prosa scan | Boyce–DiPrima 801 (indeks 785) | Baik | urutan 3 kolom benar; beberapa nomor halaman hilang/salah |
| 9 | rumus | Kardar 285 (273) | Cukup | integral pertama rusak; `e^{βε}`→`e^{bε}`; tanda aksen `q'` hilang |
| 10 | rumus | Nocedal–Wright 258 (238) | Baik | pola sparsitas Hessian kehilangan struktur kosongnya |
| 11 | rumus | Kardar 253 (241) | Buruk | `Φ_eff` terbaca `Φ_ext` di seluruh halaman (dua besaran berbeda); `ρ_ext`→`p_ext`; aksen hilang |
| 12 | rumus | Brunton–Kutz 86 (66) | Baik | `ω^{2(n−1)}` jadi `ω_n^2(n-1)`; topi `f̂` hilang 2× |
| 13 | rumus | Åström–Murray 149 (4-29) | Buruk | tanda & subskrip matriks salah; persamaan `da_l/dt` **hilang**; `α_l`→`a_l` |
| 14 | rumus | Åström–Murray 198 (6-8) | Buruk | `ω0²`→`ω0` (3×), `q̇`→`q̈`, `ω_d`→`ω0` sehingga "ω0 = ω0√(1−ζ²)" |
| 15 | bertanda | Feller 60 (42) | Gagal | ±75% isi hilang; ±11 ribu karakter `α β β β` tersisa; peluang `6/125, 1/35`→`9/15, 3/5` |
| 16 | bertanda | Fisher 1925, 188 (174) | Gagal | tabel diputar 90°; isi hanya "174" ratusan kali |
| 17 | bertanda | Murphy PML1 280 (250) | Buruk | rapi sampai (7.114); (7.115) rusak, (7.116–7.117) hilang, sisa `\mathbf{0}` berulang |
| 18 | bertanda | Boyce–DiPrima 493 (477) | Gagal | ±85% isi hilang; ±4 ribu karakter `0000…` |
| 19 | pendek | Pathria 9 (xii) | Baik | sempurna |
| 20 | pendek | Vapnik 168 (148) | Baik | keterangan gambar sempurna |

## Ringkasan
- Prosa (kalimat biasa) terbaca baik di hampir semua halaman yang tidak bertanda: pencarian makna dan kata kunci aman.
- **Rumus tidak bisa dipercaya tanpa melihat gambar**: 11 dari 14 halaman tak bertanda punya salah simbol; 4 di antaranya mengubah makna (Pathria, Kardar Φ_eff/Φ_ext, Åström ω_d, Boyce persamaan karangan). Aturan skill sudah benar: cek `get_page(..., image=True)` sebelum mengutip persamaan.
- **Halaman bertanda (101 hlm.) rusak berat**: 3 dari 4 kehilangan sebagian besar isinya.

## Bug yang ditemukan dan diperbaiki
- `rag/loopdetect.py` hanya memotong 1.500 karakter terakhir, sehingga pengulangan yang lebih panjang tetap tersimpan; deret angka ("2.0000…", "174 174…") tidak terdeteksi karena angka disamarkan jadi `#`. Diperbaiki (cadangan `loopdetect.py.bak-20261117`): potong berulang sampai bersih + periksa teks asli + deret satu karakter.
- Diterapkan hanya pada halaman bertanda (yang memang mentok batas token): **52 halaman, 202.896 karakter sampah dibuang**. Teks lama disimpan di `text_before_retrim_20261117`. Pada 10 halaman tak bertanda pemotong baru akan memotong tabel angka yang sah, jadi halaman itu sengaja tidak disentuh.

## Belum dikerjakan
- Isi yang hilang di halaman bertanda tidak kembali dengan pemotongan; perlu OCR ulang dengan cara lain (mis. memotong halaman jadi dua bagian, atau mesin OCR lain) — keputusan pengguna.
- `ocr.py --redo` memilih hasil yang **lebih panjang** bila keduanya bermasalah, sehingga hasil yang macet berulang bisa menang. Perlu dibetulkan sebelum `--redo` dipakai lagi.

## Cek ulang setelah OCR belah-dua (2026-11-18)
Teks baru dibandingkan dengan gambar halaman yang sudah dilihat kemarin.
| Halaman | Sebelum | Sesudah |
|---|---|---|
| Feller 60 (42) | Gagal: ±75% hilang | **Baik**: seluruh halaman terbaca, termasuk catatan kaki; peluang Fermi-Dirac 1/10 benar |
| Fisher 1925, 188 (174) | Gagal: "174" ratusan kali | **Cukup/Buruk**: tabel t (n=1–30, P=.9–.3) sekarang terbaca dan cocok dengan gambar (titik desimal "·" terbaca "-"); tetapi di depannya ada deret angka menurun 1886…1000 yang **tidak ada** di halaman (karangan model) |
| Murphy PML1 280 (250) | Buruk: (7.115–7.117) hilang | **Baik**: lengkap sampai (7.117) dan catatan kaki |
| Boyce–DiPrima 493 (477) | Gagal: ±85% hilang | **Baik**: soal 1–3 dan Tabel 8.5.5 lengkap |

## 4 halaman yang tidak membaik dengan belah-dua (2026-11-18)
| Halaman | Masalah | Tindakan | Hasil |
|---|---|---|---|
| Fisher 1935, 141 (129) | berhenti di judul Tabel 17; tabel + §47 hilang | OCR belah-tiga | **Lengkap**: Tabel 17 (35, 6, 12, 6, 264, total 323) + paragraf §47 |
| Taylor solusi, 210 (199) | jawaban 12.24 (a)–(c) hilang | OCR belah-tiga | **Lengkap** sampai (c) |
| Brunton–Kutz, 328 (308) | macet di matriks Koopman; (7.80) + paragraf penutup hilang | teks digital PDF dipakai (`engine: pymupdf-digital`, OCR lama di `text_before_digital`) | prosa lengkap; rumus berantakan tata letaknya |
| Nesterov, 5 (iv) | halaman hak cipta, 2 baris terakhir hilang | dibiarkan | — |
