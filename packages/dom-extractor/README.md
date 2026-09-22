# `@eng-universe/dom-extractor`

Extract article content and metadata from raw HTML with the trained Engineering
Universe DOM model. The package runs locally in Node.js. It does not call a
service and does not require Python or PyTorch at runtime.

## Install

From this repository:

```bash
cd packages/dom-extractor
npm install
```

To consume the local package from another Node.js project:

```bash
npm install ../engineering-universe/packages/dom-extractor
```

After the package is published:

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

## Update the bundled model

Train the Python model first. Then export the checkpoint from this directory:

```bash
npm run export:model -- \
  --checkpoint ../../data/learned_extraction/model/best.pt
npm test
```

The export command writes `src/model.generated.js`. Commit that generated file
with the package so npm consumers receive the checkpoint weights.
