# RDC LC Stamper — measured speed

Measured on the development laptop (8 logical CPUs, Windows 11), 2026-11-25, with the real engine
(`engine.run_batch`) and a process pool like the job worker's. Pages per second are for the whole batch: read every
page, decide, stamp, write the report. All pages had a text layer, so no OCR ran.

| batch | pages | pool of 1 | pool of 2 | pool of 4 |
|---|---:|---:|---:|---:|
| `Mu1.pdf`, stamp (124-invoice list) | 108 | 1.5 s | 1.4 s | 1.4 s |
| `Mu1.pdf`, filter only | 108 | 0.5 s | 0.3 s | 0.2 s |
| 100-page slice of the Mumbai PDF (267-invoice list) | 100 | 2.1 s | 1.5 s | 1.2 s |
| `stress_output_5000.pdf` (no invoice of the list in it) | 5000 | 16.0 s | 10.0 s | 6.0 s |
| `Mu1` + `Mu1` + Mumbai slice, one combined PDF | 316 | 1.8 s | 1.4 s | 1.0 s |

Three batches (each `Mu1.pdf`, 108 pages) started at the same moment on a pool of 4 all finished in 1.7 s.

Scanned pages (no usable text) are the slow case, because OCR is the only way to read them:

- offline OCR, header crop (top 46% of the page): about 3 s per page in the pool;
- offline OCR, whole page at 150 dpi, one thread: about 8-9 s per page (only used when the header is not enough or
  amount matching is on);
- the online tier, when a key is set, is one HTTP call per page and is tried before the offline engine.

OCR runs only for a page that is a scan, has garbled or outlined text and did not give the invoice number from its
own text, so a normal text PDF never pays this cost.

## Online OCR calls in parallel

A scanned page costs one online call of about 1.4 s, which is waiting for the network. Each worker keeps up to 3
calls in flight (`LC_ONLINE_PARALLEL`). Measured with a simulated 1.4 s service on an 18-page scan:

| calls in flight per worker | pool of 1 | pool of 2 |
|---:|---:|---:|
| 1 (before) | 26.8 s | 14.1 s |
| 3 (now) | 14.0 s | 5.5 s |

Three simultaneous calls against the real service were all accepted (no rate-limit answer).
