# Disposable npm extraction playground

This small app tests the **installed npm tarball**, not the Python package or
the package's source files. It uses a plain HTML/CSS/JavaScript page and a Node
HTTP server. The browser requests inference from the server because the package
is a Node runtime, not a browser bundle.

From the repository root, create the tarball and install it into this isolated
consumer project:

```bash
cd packages/dom-extractor
npm ci
npm pack
cd ../../npm-test
npm install --offline
```

To include the ten sample pages, point the app at a
`data/learned_extraction/raw` directory containing `manifest.json`, `html/`,
and `annotations/`:

```bash
ANNOTATION_DATA_DIR=/absolute/path/to/eng-universe/data/learned_extraction/raw npm start
```

Without sample data, the app still starts and accepts uploaded HTML files.

Open [http://127.0.0.1:8770/](http://127.0.0.1:8770/). The app runs all ten
sample pages on load. Select **Upload HTML** to add a local `.html` or `.htm`
file at the top and run it immediately. Uploads are limited to 10 MB and stay
in the browser until the page is closed. Use a page's **Run** button or **Run all**
to repeat inference.
The progress panel reports median and average inference latency per page. Each
field card shows the selected node, a snippet, and the model's confidence score.
Click a card to inspect its full extracted **Text** or selected-node **HTML**.
Article text preserves paragraph breaks. Confidence is an uncalibrated model
score, not a measured probability of correctness. The HTML tab shows escaped
markup from the npm runtime; it may differ byte-for-byte from Python's
serialization even if both select the same node. **Payload** opens the complete
JSON object returned by the installed package. **Raw HTML** opens the input
as plain text, and **Source** opens the original URL for a saved sample. Hover
or focus the candidate count and model version to see what they mean. The app
never changes the annotation files.

`PORT` can override port 8770. The sample list in `server.mjs` is intentionally
fixed to ten reviewed pages. This directory is a throwaway consumer test, not
part of the publishable package.
