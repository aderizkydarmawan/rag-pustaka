# Aturan Proyek RAG

RAG di proyek ini adalah **asisten pembawa bahan, bukan penentu arah riset**. RAG membantu mencari bahan, sedangkan penalaran dan keputusan riset tetap mengikuti alur riset yang benar.

## 1. Arah riset dikunci dulu
- Sebelum memakai RAG, pastikan tujuan riset, pertanyaan riset, dan ruang lingkupnya sudah jelas. Kalau belum ada, tanyakan ke pengguna atau rumuskan dulu dari tinjauan luas (`litreview`, `research-gap-novelty`). Jangan merumuskannya dari hasil RAG.
- RAG dipakai untuk mencari detail dan bukti, bukan untuk mencari ide atau menentukan arah riset.
- Kalau jawaban mulai menyimpang dari tujuan atau ruang lingkup, beri tahu pengguna.

## 2. Cara membaca chunk
- Jangan menganggap urutan hasil sebagai urutan pentingnya isi. Peringkat hanya menunjukkan kemiripan, bukan bobot bukti.
- Sebelum sebuah chunk dipakai untuk mendukung klaim atau keputusan, baca konteks di sekitarnya di dokumen aslinya (paragraf atau bagian sebelum dan sesudahnya).
- Sebutkan sumber setiap chunk yang dipakai: nama file dan halaman.

## 3. Melawan bias konfirmasi
- Untuk setiap klaim penting, jalankan juga pencarian dari sisi yang berlawanan, misalnya "keterbatasan X", "X tidak efektif", atau "kritik terhadap X".
- Kalau ada sumber yang saling bertentangan, tuliskan perbedaannya. Jangan memilih yang paling sesuai saja.
- Periksa sebaran hasil. Kalau sebagian besar chunk berasal dari satu paper atau satu kelompok penulis, sebutkan itu sebagai keterbatasan.

## 4. Koleksi bukan dunia
- Koleksi RAG hanya berisi yang dikumpulkan pengguna. Untuk klaim penting, cek juga ke luar koleksi (PubMed/OpenAlex lewat `litreview` atau `deep-research`) apakah ada bukti yang lebih kuat atau lebih baru.
- Kalau koleksi tidak memuat jawabannya, katakan begitu. Jangan mengisinya dari ingatan tanpa memberi tanda.

## 5. Transparansi pengaruh
- Kalau sebuah keputusan atau kesimpulan dipengaruhi chunk tertentu, tulis secara eksplisit: "dipengaruhi oleh [file, halaman]".
- Bedakan dengan jelas tiga jenis informasi: **dari RAG**, **dari pencarian luar**, dan **dari pengetahuan model**.
- Gunakan tingkat keyakinan: "terbukti", "ada indikasi", atau "dugaan".

## 6. Keputusan besar tidak berdasar RAG saja
- Mengganti metode, fokus, atau pertanyaan riset tidak boleh hanya berdasarkan hasil RAG. Uji dulu lewat `methodology-review` atau `llm-council`, lalu ikat ke pustaka yang lebih luas.
- Bila perlu, bandingkan jawaban dengan dan tanpa RAG. Kalau kesimpulannya berbeda jauh, periksa penyebabnya sebelum melanjutkan.

## 7. Verifikasi
- Sitasi yang akan masuk naskah dicek dengan `citation-verification`.
- Angka atau hasil analisis yang akan dilaporkan dicek dengan `research-validity-audit` dan dicatat dengan `experiment-log`.

## 8. Medis
- Jawaban medis adalah bahan belajar dan referensi, bukan diagnosis atau pengganti dokter.
