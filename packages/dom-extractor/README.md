# `@eng-universe/dom-extractor`

A standalone Node.js package that selects article body, title, date, and byline
nodes from a full web page. It bundles HTML processing and the trained model.
Consumers do not need Python, Go, a service, or runtime npm dependencies.
Importing the package does not load the model weights. The first valid
extraction loads them once; later calls reuse the loaded model.

## Install

```bash
npm install @eng-universe/dom-extractor
```

For this repository's unpublished build:

```bash
cd packages/dom-extractor
npm ci
npm test
npm pack
# Then install eng-universe-dom-extractor-0.5.0.tgz in your application.
```

## Extract one page

```js
import { readFile } from "node:fs/promises";
import { extract, modelVersion, ExtractError } from "@eng-universe/dom-extractor";

const html = await readFile("article.html", "utf8");
const result = await extract(html);
console.log(modelVersion, result.fields.title.text, result.fields.body.text);

try {
  await extract("");
} catch (error) {
  if (error instanceof ExtractError) console.error(error.code, error.message);
}
```

Pass full-page HTML as a decoded string, for example `response.text()` or
`readFile(path, "utf8")`. The response always has `modelVersion` and four
fields: `title`, `body`, `date`, and `byline`. Each field has
`{ text, confidence }`; `date` also has `iso`, a `YYYY-MM-DD` string or `null`.
Text is usually `null` if missing, empty, or below the internal confidence
threshold of **0.5**. A guarded date rescue can return text below that
threshold. Confidence is an uncalibrated score, rounded to four decimal places.
You can apply a stricter filter yourself.

The package keeps selected date text as displayed and parses an unambiguous
calendar date into `date.iso`. It reads `datetime` from textless `<time>`
elements. Ambiguous numeric dates and relative dates such as `3 days ago` have
`iso: null`. It does not parse author names. Normal body whitespace is
collapsed while `<pre>` and inline
preformatted whitespace is preserved.

## Extract many pages

```js
import { extractMany } from "@eng-universe/dom-extractor";

const { modelVersion, results } = await extractMany(htmlStrings);
for (const item of results) {
  if (item.status === "ok") console.log(item.result.fields.title.text);
  else console.error(item.error.code, item.error.message);
}
```

Results preserve input order. One bad page does not fail the batch. The package
processes internally in chunks of 100; for very large jobs, call
`extractMany` in chunks of about 100 pages yourself to bound memory.
`modelVersion` appears once, at the batch root. A non-article page is not
an error; fields can be `null` or low-confidence.

Inputs must be strings. Empty/whitespace input, input over 10 MB, or HTML with
no usable DOM returns `emptyInput`, `inputTooLarge`, or `parseError`.
Unexpected scoring failures return `inferenceError`; other failures return
`internalError`. `extract()` throws `ExtractError`; `extractMany()`
reports errors per item. An unknown `include` option rejects the whole call.

## Inspect a selection

```js
const result = await extract(html, { include: ["html", "source", "debug"] });
console.log(result.fields.title.html); // exact selected source substring
console.log(result.fields.title.source.selector); // unique selector in input HTML
console.log(result.debug.rejected); // text of below-threshold candidates
```

`include` accepts only `html`, `source`, and `debug` (default `[]`).
`html` is added only to accepted fields. `source` is added when a candidate
exists, including rejected candidates. `debug` adds rejected candidate text
at result level. Each option adds only its own data.

The base scorer considers every cleaned DOM candidate on a page; there is no
page-wide candidate cap. The author-boundary refiner compares at most 192
nodes near its initial author candidate. The raw input must be a full page.
The model can be wrong, so verify fields where accuracy matters.

See `schema.json` and `examples/sample.html` for the result contract.

## Model build and package size

Python is used only to train and export model weights. `npm run build`
bundles the JavaScript runtime and includes `src/model.weights.bin`; no
external model file is fetched. Run `npm pack --dry-run` to measure the
current installed and compressed sizes. The HTML parser is the main size cost.

The current npm tarball is about 137 KB compressed, with no runtime npm
dependencies. Local HTML pages and labels are not part of the package.

## Runtime measurement

For a repeatable runtime measurement, first run `npm run build`, then use
`node scripts/benchmark_full.mjs DATASET_DIR OUTPUT_JSON [REFERENCE_JSON]`.
It warms ten pages and times three sequential passes over the dataset, from
raw HTML strings to extracted fields. `scripts/audit_features.mjs` hashes the
feature arrays for the same pages. The earlier API measured 14.70 ms before
and 8.63–8.97 ms after optimization on 659 pages. With the new public API,
the optimized runtime measured 8.79 ms median on the same 659 pages (Node 20,
three sequential passes). The model weights are unchanged.
