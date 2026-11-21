# rag-pustaka

A local, evaluated RAG system for a personal library of scanned and digital PDFs
(textbooks and papers), served to Claude through an MCP server.
Everything (OCR, embeddings, reranking, the index) runs on one consumer laptop GPU (RTX 3070, 8 GB).

It was built for a self-study library that runs from basic mathematics, physics and statistics
up to AI/ML/DL: 269 documents, 45,390 pages and 61,882 chunks, about a quarter of it old scanned books.

> The library itself is **not** in this repository. It contains copyrighted books.
> This repo holds the code, the evaluation set, the results and the design notes.

## What it does

```
PDF ──► PyMuPDF text layer ──► page quality check ──► OCR the bad pages (PaddleOCR-VL 1.6, GPU)
                                                         │  loop/repetition detection
                                                         │  re-OCR dense pages split into 2–3 bands
                                                         ▼
                        structure-aware chunks (≈350 words, file + printed page kept)
                                                         ▼
          LanceDB:  BM25 full-text  +  Qwen3-Embedding-4B (nf4)  ──RRF k=60──►  Qwen3-Reranker-4B (top 30)
                                                         ▼
                 MCP server ─► Claude: search · search_exact · get_page(image) · get_context ·
                                       read_section · verify_citation · list_documents · get_card
```

Design choices that matter:

- **Citations are pages, not chunks.** Every hit carries the file and the printed page, plus the PDF page when the
  printed number is uncertain. `verify_citation` checks that a quote really appears on that page.
- **Anti-confirmation-bias rules** for the assistant ([docs/anti-bias-rules.md](docs/anti-bias-rules.md)): read the
  surrounding context, search for counter-evidence, and report when most hits come from one source.
  Rank shows similarity. It is not a measure of evidence weight.
- **Honest coverage.** The server reports how many pages are still unsearchable. Dense search switches itself off if
  fewer than 98% of chunks have vectors, so the system never silently mixes indexed and unindexed text.
- **Per-folder privacy.** A `private` folder returns only metadata, so no document text is sent to the LLM. New folders
  must be decided explicitly when they are registered, and an undecided folder is treated as private.
- **Metadata via DOI only.** OpenAlex lookup uses DOIs alone (see the trial below).

## Evaluation

70 questions with answer keys ([eval/questions_v1.json](eval/questions_v1.json)), written *without* looking at the
retrieved chunks:
A = facts from specific papers (the query names neither title nor author), B = textbook concepts,
C = answers that sit in OCR'd scanned books, D = out-of-collection questions that should return nothing.
Grading is automatic: a hit counts only if it comes from an accepted document **and** matches the answer regex.

Results on A+B+C (n = 62), with 95% bootstrap CIs:

| Config | ans@1 | ans@3 | ans@10 | MRR@10 | median s/query |
|---|---|---|---|---|---|
| BM25 only | 0.42 (0.29–0.55) | 0.61 (0.48–0.73) | 0.81 (0.71–0.90) | 0.54 | 0.01 |
| Dense only | 0.65 (0.52–0.76) | 0.84 (0.74–0.92) | 0.97 (0.92–1.00) | 0.76 | 0.44 |
| Hybrid (RRF) | 0.65 (0.52–0.76) | 0.82 (0.73–0.92) | 0.98 (0.95–1.00) | 0.76 | 0.43 |
| **Hybrid + rerank** | **0.66** (0.55–0.77) | **0.92** (0.84–0.98) | **0.98** (0.95–1.00) | **0.78** | 10.1 |

Read with care:

- Dense retrieval does most of the work, and BM25 alone misses about one answer in five within the top 10.
- The reranker mainly lifts ans@3 (0.82 → 0.92). The confidence intervals overlap, and it costs ~10 s per query on 8 GB.
- On the out-of-collection questions (D) the top rerank score stayed ≤ 0.001, which makes a usable "not in the
  library" signal. It is not a perfect one: 2 of the 62 answerable questions also scored below 0.11.
- The questions and answer keys were drafted with AI assistance and graded automatically in a single run,
  so these are development numbers, not a final evaluation.
- These are 62 questions on one library. Treat the numbers as indicative.

Full numbers: [eval/results/summary_20261117.json](eval/results/summary_20261117.json).

## Problems solved along the way

- **OCR that loops.** Vision-language OCR sometimes repeats one line until it hits the token limit.
  [`rag/loopdetect.py`](rag/loopdetect.py) detects this, including character runs and repeated number sequences, and
  trims it. That removed ~200k characters of junk from 52 pages.
- **Dense pages that never finish.** Long tables and formula-heavy pages are re-OCR'd in 2 or 3 horizontal bands.
  The cuts fall in the emptiest strip near each boundary, so no text line is split
  ([`rag/ocr_split.py`](rag/ocr_split.py)). 97 of 101 flagged pages improved; 2 more were fixed with 3 bands.
- **Choosing between OCR attempts.** "Longer output wins" picked looping garbage. The fix ranks clean output first,
  then the number of *distinct* words after trimming.
- **A visual audit of OCR** ([eval/ocr_visual_check_20261117.md](eval/ocr_visual_check_20261117.md)): prose is
  reliable, but formulas in scans are not, so the assistant must look at the page image before quoting an equation.
- **Title lookup is a trap** ([eval/openalex_trial/REPORT.md](eval/openalex_trial/REPORT.md)): over 20 sampled
  documents, OpenAlex by DOI was right 5/5 and by title 0/15. Title search returned book reviews, retrospectives or
  other papers that cite the title. So lookup is DOI-only, and "not checked" is never shown as "not retracted".

## Layout

```
rag/          extract.py · ocr.py · ocr_split.py · loopdetect.py · library.py (chunk + index) ·
              embed.py · rerank.py · enrich.py (DOI metadata) · settings.py (privacy) · server.py (MCP)
eval/         questions, grader, results, OCR audit, OpenAlex trial
docs/         SPEC.md (full design spec, Indonesian) · anti-bias-rules.md
integrations/ Claude Code skill that tells the assistant how to use the tools
scripts/      download_models.py
```

## Running it

Requires Python 3.12, an NVIDIA GPU with ≥ 8 GB and CUDA.

```bash
pip install -r requirements.txt            # install torch for your CUDA version first
python scripts/download_models.py          # ~20 GB into ./models (or set RAG_MODELS)

python -m rag.extract  "<pdf folder>"       # text layer + page quality report
python -m rag.ocr      "<pdf folder>"       # OCR pages that need it (resumable, Ctrl+C safe)
python -m rag.settings "<pdf folder>"       # decide: private? DOI lookup?
python -m rag.enrich   "<pdf folder>"       # optional, DOI-only metadata
python -m rag.library build "<pdf folder>"  # chunk + BM25 index, registers the library
python -m rag.embed    "<pdf folder>"       # dense vectors (cached per chunk)
```

Register the MCP server with Claude Code:

```bash
claude mcp add rag-pustaka -- python -m rag.server
```

Code comments and the spec are in Indonesian, because the project was built for an Indonesian-speaking user.

## License

Code: MIT. The models keep their own licenses (PaddleOCR-VL: Apache-2.0; Qwen3 Embedding/Reranker: Apache-2.0).
