# Project Configuration for Claude Code

## Project Structure & Module Organization

- **Source code**: `eng_universe/` - main Python package
- **Modules**:
  - `ingest/` - data acquisition (crawler, robots.txt, queue, ETL)
  - `index/` - indexing pipeline (Redis search index, worker, entity extraction)
  - `search/` - search operations (search execution, embeddings, ColBERT/Pylate)
  - `monitoring/` - observability (Prometheus metrics, metrics server)
  - `extraction/` - production DOM cleanup, model inference, and postprocessing
  - `config.py` - shared configuration
- **Model development**: `modeling/dom_extractor/` - DOM extractor data,
  labeling, training, evaluation, and local playgrounds
- **Scripts**: `scripts/` - utility scripts for crawling, indexing, seeding
- **API**: `api/` - FastAPI search endpoint
- **CLI entry point**: `main.py`
- **Data storage**: `data/` - local data files
- **Package manager**: `uv` (not pip)

## Package Manager

This project uses **uv** for Python package management. Always use `uv` commands instead of `pip`:

- Install dependencies: `uv sync` or `uv pip install <package>`
- Run Python: `uv run python <script>`
- Run commands in the venv: `uv run <command>`

## Running Tests

```bash
uv run pytest
```

## Running the Application

```bash
uv run python main.py <command>
```
