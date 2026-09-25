# Disposable npm extraction playground

This small app tests the **installed npm tarball**, not the Python package or
the package's source files. It uses a plain HTML/CSS/JavaScript page and a Node
HTTP server. The browser requests inference from the server because the package
is a Node runtime, not a browser bundle.

From the demo directory, build and install the current optimized package into
this isolated consumer project:

```bash
cd dom-tiny-demo
npm run refresh-model
npm start
```

The refresh command builds the package, creates its local npm tarball, updates
the demo lockfile's tarball checksum, installs it, and checks that the installed
JavaScript matches the new build. Run it again after changing the extractor.
Restart an already-running demo server afterward: Node keeps its imported
package in memory until the process exits. The demo uses the faster JavaScript
runtime from this branch; the neural model weights are unchanged.

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
field card shows a snippet and the model's confidence score.
Click a card to inspect its full extracted **Text**, selected-node **HTML**,
or **Rendered HTML** in a sandboxed frame. The rendered preview blocks scripts
and external resources, and does not load the source site's styles.
Body text preserves paragraph breaks. Confidence is an uncalibrated model
score, not a measured probability of correctness. The HTML tab shows the exact
selected substring of the input HTML. **Payload** opens the complete
JSON object returned by the installed package, with JSON syntax highlighting.
**Raw HTML** opens the input as plain text, and **Source** opens the original
URL for a saved sample. Hover or focus the model version to see what it means.
The app never changes the
annotation files.

The package omits HTML by default; this app opts in with
`include: ["html", "source"]` so the popup can show markup. Select **Include
debug** before a run to add rejected candidate text to the payload.

`PORT` can override port 8770. The sample list in `server.mjs` is intentionally
fixed to ten reviewed pages. This directory is a throwaway consumer test, not
part of the publishable package.
