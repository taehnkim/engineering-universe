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

Point the app at a `data/learned_extraction/raw` directory containing
`manifest.json`, `html/`, and `annotations/`:

```bash
ANNOTATION_DATA_DIR=/absolute/path/to/eng-universe/data/learned_extraction/raw npm start
```

Open [http://127.0.0.1:8770/](http://127.0.0.1:8770/). Click a page's **Run**
button or **Run all 10**. The results compare each predicted node ID with the
human-reviewed annotation and show a short content snippet plus the model's
confidence score. Click any result card to inspect its full extracted **Text**
or selected-node **HTML** in a popup. Article text preserves paragraph breaks.
Confidence is an uncalibrated model score, not a measured probability of
correctness. The HTML tab shows escaped markup from the npm runtime; it may
differ byte-for-byte from Python's serialization even if both select the same
node. The page-level **HTML** link opens the saved raw input as plain text;
**Source** opens the original URL. The app never changes the annotation files.

`PORT` can override port 8770. The sample list in `server.mjs` is intentionally
fixed to ten reviewed pages. This directory is a throwaway consumer test, not
part of the publishable package.
