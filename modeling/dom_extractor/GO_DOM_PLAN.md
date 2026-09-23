# Go DOM optimization plan

The current inference path parses and cleans raw HTML with BeautifulSoup, builds
features from `Tag` objects, runs the checkpoint, and renders a labeled preview.
Labels and checkpoints depend on node IDs assigned **before** cleanup. The
author refiner also traverses the same `Tag` tree. These are compatibility
constraints, not implementation details.

1. Measure warm end-to-end latency and each stage on representative pages.
2. Remove the duplicate Python parse in preview generation without changing
   node IDs, predictions, or rendered HTML.
3. Prototype Go parsing and cleanup behind the `ParsedPage` seam. Pass a
   compact prepared HTML buffer with stable IDs back to Python; retain Python
   feature extraction and author refinement for now. Use an in-process native
   adapter rather than spawning `go run` for each request.
4. Compare candidate IDs, selected content, feature matrices, predictions, and
   preview links against Python across all reviewed pages and malformed HTML
   fixtures. Measure the adapter and Python reparse together, not Go alone.
5. Promote Go only if parity is exact and warm end-to-end latency is lower than
   the single-parse Python path. Keep Python as the default/fallback otherwise.

Fixed-size integer buffers should be considered for a later Go feature encoder
only after this parity gate. Passing buffers does not help if Python still has
to reconstruct and traverse the full DOM to compute the same features.
