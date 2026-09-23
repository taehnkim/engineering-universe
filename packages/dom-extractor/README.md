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
npm install /path/to/eng-universe/packages/dom-extractor/eng-universe-dom-extractor-0.1.0.tgz
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
console.log(result.article?.text);
console.log(result.date?.text);
console.log(result.summary?.text);
console.log(result.relative_date?.text);
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

The six extracted fields are `article`, `title`, `authors`, `date`, `summary`,
and `relative_date`. A field is `null` when the model selects its learned
missing option. Otherwise it has `{ nodeId, html, text }`.

`scrapedAt` defaults to the current time. Pass the actual page-fetch timestamp
when possible. The package uses it to turn relative publication dates such as
`2 days ago` into `publishedAt`. Reading times such as `5 min read` are not
treated as relative dates.

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

This accuracy-preserving build is **not under 100 KB installed**. The measured
package is about 332 KB unpacked and 135 KB as an npm tarball. It has
no runtime npm dependencies. The bundled checkpoint weights are 29,916 bytes;
the minified JavaScript is 282,204 bytes. Third-party license notices add
13,312 bytes. The parser and DOM behavior are the main cost: minifying the HTML
parser plus DOM cleanup module produced about 263 KB. Packaging only
the weights would be smaller, but could not accept raw HTML.

On the 659 human-reviewed pages in the local corpus, this JavaScript build
selected exactly the same node IDs as the unchanged Python checkpoint for all
six fields. The model was not retrained. The development-only parity audit is
`node scripts/audit_corpus.mjs DATASET_DIR PYTHON_REFERENCE_JSON`; those local
HTML files and labels are not part of the npm package.

A sub-100 KB standalone runtime would need a substantially smaller HTML parser
or a different model/runtime design. Either can change node IDs, so it should
be a separate experiment with a full-corpus parity gate. A thin client for a
remote inference service could also be under 100 KB, but would no longer be
standalone.
