# `@eng-universe/dom-extractor`

Extract article content and metadata from raw HTML with the current Engineering
Universe DOM checkpoint. This is a standalone Node.js package: the HTML parser,
cleanup, feature encoder, model scorer, author-boundary refiner, and weights are
included. It makes no network call and requires neither Python nor Go at runtime.

## Install

Build and test from this repository:

```bash
cd packages/dom-extractor
npm ci
npm test
npm pack
```

To consume the package locally from another Node.js project:

```bash
npm install /path/to/eng-universe/packages/dom-extractor/eng-universe-dom-extractor-0.2.0.tgz
```

After publication:

```bash
npm install @eng-universe/dom-extractor
```

## Use

```javascript
import { readFile } from "node:fs/promises";
import { extract } from "@eng-universe/dom-extractor";

const html = await readFile("article.html", "utf8");
const result = await extract(html, {
  scrapedAt: "2026-09-21T12:00:00Z",
});

console.log(result.title?.text);
console.log(result.authors?.text);
console.log(result.article_text); // paragraphs retain newlines
console.log(result.article_html); // selected wrapper and its markup
console.log(result.article_confidence); // uncalibrated model score share
console.log(result.date_text);
console.log(result.publishedAt);

// Each non-missing field also includes the selected wrapper.
console.log(result.article?.html);
console.log(result.article?.nodeId);

// Useful when inspecting preprocessing and model behavior.
console.log(result.predictions);
console.log(result.diagnostics);
```

No other program is launched by `extract()`. The `npm test` suite installs the
packed artifact into a temporary project and calls it with Python and Go absent
from `PATH`.

The four extracted fields are `article`, `title`, `authors`, and `date`. Each
field has top-level `<field>_text`, `<field>_html`, and `<field>_confidence`
values. The existing `result.article`-style selection is also available;
when present it has `{ nodeId, html, text, confidence }`. Missing fields are
`null`. Article text retains paragraph boundaries. Confidence is a softmax
share of the final node's base-model logit across candidates and the missing
option. It is **not calibrated** to correctness; the author-boundary ranker can
move the selected author node after base scoring.

`scrapedAt` defaults to the current time. Pass the actual page-fetch timestamp
when possible. The date output represents absolute publication dates only;
`publishedAt` is the selected date text when absolute, otherwise `null`.
The legacy `summary` and `relative_date` labels remain in local annotation
files for history, but they are not model outputs.

To extract only one field:

```javascript
import { extractField } from "@eng-universe/dom-extractor";

const article = await extractField(html, "article");
console.log(article?.text);
```

## Run the included sample

The repository includes a small HTML input and its captured output:

```bash
node examples/run-sample.mjs
```

See `examples/sample.html` for the input and `examples/sample-output.json` for
the expected result. This sample uses a fixed `scrapedAt` value, so its output
is repeatable.

## Update the bundled model

Train the Python model first. Then export both compatible checkpoints from this
directory:

```bash
npm run export:model -- \
  --checkpoint ../../eng_universe/extraction/checkpoints/best.pt \
  --author-boundary ../../eng_universe/extraction/checkpoints/author_boundary.pt
npm test
```

The exporter checks that the author-boundary checkpoint matches the base model.
It writes `src/model.generated.js` and `src/model.weights.bin`. `npm run build`
bundles the JavaScript and copies the binary weights into `dist/`. Python is
used only for training and this export step, never for consumer inference.

## Size and parity

This standalone package is **not under 100 KB installed**. Its measured npm
tarball is about 135 KB and its unpacked size is about 333 KB. The minified
JavaScript is 283,048 bytes (102,020 bytes gzipped); weights are 29,612 bytes
(27,643 bytes gzipped). It has no runtime npm dependencies. The HTML parser
and DOM behavior dominate the size. Packaging only the weights would be
smaller, but could not accept raw HTML.

The development-only full-corpus parity audit is
`node scripts/audit_corpus.mjs DATASET_DIR PYTHON_REFERENCE_JSON`; those local
HTML files and labels are not part of the npm package.

A sub-100 KB standalone runtime would need a substantially smaller HTML parser
or a different model/runtime design. Either can change node IDs, so it should
be a separate experiment with a full-corpus parity gate. A thin client for a
remote inference service could also be under 100 KB, but would no longer be
standalone.
