# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

LightRAG-EDP — a fork of [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG) v1.5.5 for enterprise document parsing and intelligence analysis. Deployed as an offline Docker stack (x86_64 / ARM64). Upstream PRs target `HKUDS/LightRAG`, not this fork.

The deployment package lives in `fixed-pkg/`. The LightRAG source tree is `fixed-pkg/lightrag/` (recently reorganized: docs → `docs/`, Docker/compose files → `docker/`, deploy configs → `deploy/`).

## Key EDP Customizations (this fork's value-add)

- **S-strategy chunking** (`lightrag/edp/chunk_strategy_s.py`): heading-tree-based structural chunking with Chinese numbering detection. Activated via `PROCESS_OPTION_CHUNK_STRUCTURE` ("S") in `constants.py`.
- **Parent-child section recall**: `SECTION_RECALL=true` retrieves sibling chunks from the same heading section.
- **YAML profile system** (`lightrag/edp/profiles/`): per-document-type parsing config (chunk size, heading levels, metadata fields). Selected via `EDP_DEFAULT_PROFILE` env var.
- **Milvus metadata filtering**: multi-source metadata (document/heading/LLM/rules) stored in Milvus dynamic fields with native scalar-expression filtering.
- **Chinese entity type prompts**: custom YAML entity extraction types via `ENTITY_TYPE_PROMPT_FILE`.
- **Parser routing**: `LIGHTRAG_PARSER` env var maps file extensions to engines (native/mineru/docling/legacy) with process options.

## Deployment Model

The production stack is defined by `fixed-pkg/docker-compose.deploy.yml` — 5 containers:
- `lightrag-edp` (lightrag-base:latest, port 9621)
- `milvus-standalone` + `milvus-etcd` + `milvus-minio` (vector DB stack)
- `attu` (Milvus UI, port 3000)

### Docker Image

Built locally from `fixed-pkg/Dockerfile` (pip install, no venv), **not** from `ghcr.io/hkuds/lightrag`. The local image uses system site-packages at `/usr/local/lib/python3.12/site-packages/`. Key differences from ghcr.io image: single `pip install` pass, no venv overhead, ~3.6GB vs ~4.9GB.

Build with Tsinghua PyPI mirror (the Dockerfile already has `-i https://pypi.tuna.tsinghua.edu.cn/simple`).

### Source Hot-Update (entrypoint mechanism)

The entrypoint at `fixed-pkg/lightrag/docker-entrypoint.sh` syncs mounted source into site-packages at container start:
1. `rm -rf` + `cp -r` for each subdirectory (api, chunker, edp, evaluation, kg, llm, parser, sidecar, tools)
2. `cp` all root-level `.py` files
3. `rm -rf` + `cp -r` for EDP profiles (so deleted profiles disappear)
4. `cp -r` for webui static files

After syncing source, `docker restart lightrag-edp` is all that's needed — no image rebuild. The `update.sh` script does this for running containers via `docker cp`.

### Deployment Scripts (all in `fixed-pkg/`)

| Script | Purpose |
|---|---|
| `deploy.sh` | Full deploy: load images from `images/*.tar`, create data dirs, `up -d` |
| `update.sh` | Hot-update: rsync source, docker cp frontend + profiles, restart |
| `restart.sh` / `stop.sh` / `status.sh` | Compose wrappers |
| `sync-metadata.sh` | Backport Milvus `$meta` to JSON KV storage |

### Offline Packaging

```bash
# All 5 images already saved in fixed-pkg/images/
# Build the deploy tarball:
cd fixed-pkg
tar -czf lightrag-edp-deploy.tar.gz \
  --exclude='data' --exclude='tmp' --exclude='__pycache__' \
  --exclude='tests' --exclude='docs' --exclude='examples' \
  ... lightrag/ docker-compose.deploy.yml .env images/ deploy.sh *.sh
```

Target server: `tar -xzf`, `./deploy.sh` (one command).

## Development Commands

All paths relative to `fixed-pkg/lightrag/`.

### Setup
```bash
uv sync --extra test --extra offline   # Full dev install
cd lightrag_webui && bun install --frozen-lockfile && bun run build
```

### Running the Server
```bash
lightrag-server                               # Production
uvicorn lightrag.api.lightrag_server:app --reload  # Development
```

### Testing
```bash
./scripts/test.sh tests                              # Full suite
./scripts/test.sh tests/kg/test_graph_storage.py     # Single file
./scripts/test.sh tests --test-workers 4              # Parallel
```
Test layout mirrors `lightrag/` package. Backend tests use `_impl/` subdirectory convention under `tests/kg/` and `tests/llm/`. Integration tests skipped by default (`-m "not integration"`). Enable with `LIGHTRAG_RUN_INTEGRATION=true`.

### Frontend
```bash
cd lightrag_webui
bun run dev          # Dev server
bun test             # Bun built-in runner (not Vitest)
bun run build        # Production build → lightrag/api/webui/
```

### Linting
```bash
ruff check .
```

## Architecture

### LightRAG Class Composition
```
LightRAG → _RoleLLMMixin → _StorageMigrationMixin → _PipelineMixin → object
```
`@final` on `LightRAG`. Always call `await rag.initialize_storages()` after instantiation.

### Storage Layer — 4 pluggable types
- **KV_STORAGE**: LLM cache, text chunks, doc info
- **VECTOR_STORAGE**: entity/relation/chunk embeddings
- **GRAPH_STORAGE**: entity-relation graph
- **DOC_STATUS_STORAGE**: processing status

Backend implementations in `lightrag/kg/`. Registry in `kg/__init__.py`, factory in `kg/factory.py::get_storage_class()`.

### Pipeline Concurrency
Shared `pipeline_status` dict under `get_namespace_lock()`. Key fields: `busy`, `destructive_busy`, `scanning`, `scanning_exclusive`, `pending_enqueues`, `request_pending`. Concurrent enqueue + processing is permitted; destructive ops (clear/delete) serialize against everything.

### Key Files
- `lightrag/lightrag.py`: main orchestrator
- `lightrag/pipeline.py`: ingestion pipeline
- `lightrag/operate.py`: extraction and query operations
- `lightrag/api/lightrag_server.py`: FastAPI app (`create_app` @~1202, `main` @~2764)
- `lightrag/constants.py`: process options (i/t/e/!/F/R/V/P/**S**), defaults
- `lightrag/edp/`: S-strategy chunking, heading detection, metadata, profiles
- `lightrag/parser/`: unified parser routing (native/mineru/docling/legacy)

## Configuration

Primary config is `.env` (live at `fixed-pkg/.env`, mounted into container read-only). OS env vars override `.env` values. Key groups:
- **LLM/Embedding/Rerank**: `LLM_BINDING`, `LLM_BINDING_HOST`, `EMBEDDING_BINDING`, `EMBEDDING_MODEL`, `RERANK_BINDING`
- **Storage**: `LIGHTRAG_VECTOR_STORAGE=MilvusVectorDBStorage`, `MILVUS_URI=http://milvus:19530`
- **Chunking**: `CHUNK_SIZE=1200`, `CHUNK_S_SIZE=1024`, `LIGHTRAG_PARSER=docx:native-teS;*:legacy-S`
- **EDP**: `EDP_DEFAULT_PROFILE`, `SECTION_RECALL=true`, `HEADING_MAX_LEVEL`
- **Auth**: `LIGHTRAG_API_KEY`, `AUTH_ACCOUNTS` + `TOKEN_SECRET`

`config.ini` is deprecated. Profile YAML files in `lightrag/edp/profiles/` control per-document-type parsing.

## Commit Style

- Comments, code, log messages, commit messages in English
- PRs target `HKUDS/LightRAG`
- Python: PEP 8, type annotations, `lightrag.utils.logger` instead of print, async/await throughout
