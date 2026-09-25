# Article extractor comparison

This benchmark compares the current standalone DOM Extractor package with four
widely used alternatives on the same saved, full-page HTML. It makes no network
requests during extraction. The charts in [`results/charts`](results/charts)
are 1600-pixel PNG files sized for social posts; their SVG sources are included.

| Tool | Version | Input and output adapter |
| --- | --- | --- |
| DOM Extractor | npm 0.4.0 / article-0.1.0 | `extract(html)`; `title`, `body`, `date`, `byline` |
| [Firefox Readability.js](https://github.com/mozilla/readability) | 0.6.0 + jsdom 26.1.0 | `Readability(document).parse()`; `title`, `textContent`, `publishedTime`, `byline` |
| [Trafilatura](https://trafilatura.readthedocs.io/en/latest/corefunctions.html) | 2.2.0 | `extract(html, output_format="json", with_metadata=True)`; `title`, `text`, `date`, `author` |
| [Extractus Article Extractor](https://www.npmjs.com/package/@extractus/article-extractor) | 9.0.1 | `extractFromHtml(html, url)`; `title`, `content`, `published`, `author` |
| [Newspaper4k](https://newspaper4k.readthedocs.io/en/latest/user_guide/quickstart.html) | 0.9.6 | `Article.download(input_html=html, ignore_read_more=True); parse()`; title, text, publish date, authors |

Readability.js requires a DOM implementation in Node, so its measured install
includes jsdom. Extractus returns article HTML; the benchmark converts that to
text with its own linkedom dependency. The URL recorded with each saved page is
passed to tools that accept it, but no tool fetches the URL. The DOM Extractor
receives only HTML, as its public API requires.

## Dataset and accuracy

The corpus has **659 human-reviewed pages** from **27 sites**: 429 train pages
from 18 sites, 200 validation pages from 8 sites, and 30 test pages from one
site (Meta Engineering). All tools process exactly the same raw HTML pages.
Gold text is taken from each human-selected DOM node after the project's HTML
cleanup. The raw HTML and individual predictions remain local and are ignored
by Git; only aggregate metrics are committed. The summary records SHA-256
fingerprints for the gold labels and model checkpoint.

Recorded results are below. Field columns use the **230 non-training pages**
(200 validation + 30 test); details and all-page/test-only scopes are in
[`results/summary.json`](results/summary.json).

| Tool | Installed | Median/page | Title | Body | Date | Byline |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DOM Extractor | 0.338 MB | 8.1 ms | 99.1% | 77.0% | 97.7% | 84.8% |
| Readability.js + jsdom | 13.934 MB | 34.8 ms | 88.3% | 75.7% | 54.6% | 21.4% |
| Trafilatura | 60.963 MB | 22.9 ms | 70.4% | 65.2% | 91.7% | 13.4% |
| Extractus | 5.521 MB | 19.7 ms | 67.0% | 75.7% | 75.9% | 21.4% |
| Newspaper4k | 41.333 MB | 63.1 ms | 91.7% | 64.8% | 79.6% | 12.5% |

The five ready-to-share PNG charts are [installed size](results/charts/package-size.png),
[median latency](results/charts/latency.png),
[non-training field accuracy](results/charts/accuracy-nontraining.png),
[all-page field accuracy](results/charts/accuracy-all.png), and
[the single held-out site](results/charts/accuracy-test.png).

The cross-tool measure is **text agreement**, not exact DOM-node accuracy:

- For each field, consider only pages with a present human label. Report the
  share of pages with token F1 at least 0.90. The charts show these percentages.
- Text comparison uses Unicode NFKC normalization, case folding, and word
  tokens. Date strings representing the same calendar day count as a match;
  relative dates use the saved `scraped_at` timestamp. Leading `By:` or
  `Written by` is ignored for byline comparison.
- [`results/summary.json`](results/summary.json) also reports mean token F1,
  normalized exact-text agreement, missing-label specificity, output coverage,
  and failure counts.

These labels were created for this model's DOM-selection task. Deriving text
through the project's cleanup can favor the model relative to tools that
return a separately cleaned article. In particular, a human-selected article
wrapper can contain metadata that another library intentionally removes.
Treat body scores as **agreement with this dataset**, not a universal measure
of readable article quality. Validation sites informed model selection; the
strict test split has only one site, so its high scores do not establish broad
generalization. The non-training chart combines validation and test sites and
states that limitation on the image.

## Latency and size

Each tool runs in a separate process on all 659 pages, after warming on the
first 10 pages. Per-page time starts after the saved HTML is read and includes
HTML parsing, inference/extraction, and conversion to field text. It excludes
file I/O, imports, process startup, and network latency. Runs are sequential;
they are not a concurrency-throughput test. The summary reports median, mean,
and p95. This is one run on an Apple M4 Pro Mac, not a statistical performance
study. JavaScript tools ran on Node 26.4.0; the Python tools used separate uv
environments on CPython 3.13.5.

Installed size is the sum of library files and mandatory dependencies. It
excludes the Node/Python interpreters and standard libraries, and excludes
Python bytecode generated when the benchmark ran. The DOM Extractor count
includes its bundled HTML parser and weights. `results/sizes.json` also
records its compressed npm tarball size. Cross-language footprint comparisons
remain approximate because Python and Node package layouts differ.

## Reproduce

Requires Node 20.19+ (Node 26.4.0 was used for the recorded run), `npm`, `uv`,
and the local ignored `data/learned_extraction/raw` corpus. From the repository
root:

```bash
cd packages/dom-extractor
npm ci
npm run build
cd ../../benchmarks/article-extractors
npm ci
node corpus.mjs
node run-js.mjs dom-extractor
node run-js.mjs readability
node run-js.mjs extractus
(cd python/trafilatura && uv sync --no-install-project && uv run --no-sync python ../../run-python.py trafilatura)
(cd python/newspaper && uv sync --no-install-project && uv run --no-sync python ../../run-python.py newspaper)
node score.mjs
node size.mjs
node charts.mjs
node --test test/*.test.mjs
```

The Python environments are isolated, so one library cannot silently reuse
another library's dependencies. `corpus.mjs` selects only reviewed annotations
whose labeled nodes still exist after cleanup. If a label is invalid, it
reports and skips that page instead of changing the expected answer.
