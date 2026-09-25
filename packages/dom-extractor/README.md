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
# Then install eng-universe-dom-extractor-0.4.0.tgz in your application.
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
`{ text, confidence }`. Text is `null` if missing, empty, or below the
internal confidence threshold of **0.5**. Confidence is an uncalibrated score,
rounded to four decimal places. You can apply a stricter filter yourself.

The package does **not** parse dates or author names. It returns their selected
node text as displayed, including punctuation and relative dates such as
`3 days ago`. Normal body whitespace is collapsed while `<pre>` and inline
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
