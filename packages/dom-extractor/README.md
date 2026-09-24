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
npm install /path/to/eng-universe/packages/dom-extractor/eng-universe-dom-extractor-0.3.0.tgz
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
const result = await extract(html);

console.log(result.title?.text);
console.log(result.authors?.text);
console.log(result.article?.text); // paragraphs retain newlines
console.log(result.article?.html);
console.log(result.article?.nodeId);
console.log(result.article?.confidence); // uncalibrated model score share

// Request model metadata only when needed.
const inspected = await extract(html, { debug: true });
console.log(inspected.debug?.candidateCount);
```

No other program is launched by `extract()`. The `npm test` suite installs the
packed artifact into a temporary project and calls it with Python and Go absent
from `PATH`.

The default result has exactly four keys: `article`, `title`, `authors`, and
`date`. Each field is `{ nodeId, html, text, confidence }` or `null` when the
model selects missing. Pass `{ debug: true }` to add a `debug` object with the
candidate count and model versions. The full contract is in `schema.json`.
Article text retains paragraph boundaries. Recognized HTML tables and repeated
CSS-grid comparison rows become labeled bullet lists in `text`; `html` keeps the
selected markup unchanged. Ambiguous layouts keep their original text order.
Confidence is a softmax
share of the final node's base-model logit across candidates and the missing
option. It is **not calibrated** to correctness; the author-boundary ranker can
move the selected author node after base scoring.

The date selection contains the selected node's text and markup, not a
normalized publication timestamp. The separate `resolveRelativeDate()` utility
can resolve a relative date when given a scrape timestamp. The legacy
`summary` and `relative_date` labels remain in local annotation files for
history, but they are not model outputs.

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
the expected result.

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
tarball is about 135 KB and its unpacked size is about 334 KB. The minified
JavaScript is 282,791 bytes (101,930 bytes gzipped); weights are 29,612 bytes
(27,625 bytes gzipped). It has no runtime npm dependencies. The HTML parser
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
