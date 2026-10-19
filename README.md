# Search Services Lab

A focused POC of search infrastructure exposed as tools for agents: **Vespa BM25 + Python API + official MCP SDK + web demo**.

[![Verify POC](https://github.com/n-dragon/search-services-poc/actions/workflows/ci.yml/badge.svg)](https://github.com/n-dragon/search-services-poc/actions/workflows/ci.yml)

**Status:** published on GitHub; not deployed. No live URL exists yet. This project is independent and is not affiliated with Mistral AI.

## What works in this first version

- Real Vespa lexical retrieval, no simulated fallback.
- Read-only HTTP API and MCP tools: `search_documents`, `get_document`.
- Original, versioned ten-document educational corpus with source references.
- Stable ingestion identifiers and replay-safe replacement writes.
- Bounded search concurrency, bounded query size/results and backend timeouts.
- Separate liveness/readiness endpoints; request/error/duration counters.
- Web UI and containerized bootstrap.
- Unit/API/MCP transport checks and a CI job designed to run against real Vespa.

The tiny corpus establishes integration, not retrieval quality or scale. Vector retrieval, public datasets, checkpointed ingestion, quality evaluation and load benchmarks are roadmap items.

## Start locally

Prerequisites: Docker Engine with Compose. Allocate at least 6 GB RAM and adequate disk headroom; this is a starting allocation, not a measured sizing recommendation.

```sh
docker compose up -d --build
python3 scripts/smoke.py
```

Open http://localhost:8000. First startup deploys the schema and feeds the corpus; allow several minutes for Vespa initialization.

```sh
curl 'http://localhost:8000/api/search?q=replication'
curl http://localhost:8000/readyz
curl http://localhost:8000/metrics
```

MCP Streamable HTTP endpoint: `http://localhost:8000/tools/mcp`. Use a client that supports Streamable HTTP; no API key is required for this public-data demo.

```sh
docker compose logs -f bootstrap web
docker compose down
```

`down` preserves indexed data. Use `docker compose up --force-recreate bootstrap` to replay the fixed corpus after edits. Document IDs are stable; deletion reconciliation is not implemented.

## Verify without Docker

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

These tests use a mocked Vespa HTTP transport and the actual MCP SDK server. They do not validate Vespa deployment or ranking. GitHub Actions also includes an end-to-end Docker job; its status must be checked after publishing.

## Online deployment

For the Ubuntu 4 GB VM, use the manual **Deploy demo VM** GitHub Actions workflow and follow [the SSH/secrets setup guide](docs/github-actions-deployment.md). It supports IP-only HTTP initially and domain HTTPS when configured. Deployment is gated on the reusable verification workflow, including the small-memory integration test.

See [deployment guide](docs/deployment.md). The intended deployment is a VM running the Compose stack behind Caddy HTTPS. GitHub Pages cannot run this Python/Vespa backend.

Only add a live URL to an application after the real-backend smoke test passes remotely. Record the date and commit tested. No benchmark numbers are claimed in this repository.

## Architecture and trade-offs

```mermaid
flowchart TD
    UI[Web demo] --> API[Python HTTP API]
    Agent[MCP client] --> MCP[Official MCP SDK]
    API --> Backend[Bounded search adapter]
    MCP --> Backend
    Backend --> Vespa[Vespa BM25 index]
    Feed[Bootstrap ingestion] --> Vespa
```

The service is intentionally single-node and read-only for users. There is no HA claim, user-upload endpoint, SQL/SPARQL execution, or expensive reranker. The concurrency limit is process-local: use one worker as configured. Metrics cover search calls; they are not a complete production observability stack. `get_document` shares the HTTP connection-pool and timeout limits, but has no independent semaphore.

The Vespa and Caddy images currently use major-version tags. Resolve and pin tested image digests before treating a deployment as reproducible. Python top-level dependencies are pinned; a full transitive lockfile remains to be added.

## Roadmap

1. **Release gate:** run Docker CI; deploy to a VM; verify remote search and MCP; record deployment URL and commit.
2. Add NFCorpus/BEIR ingestion with data provenance, license notes and fixed dataset versions.
3. Compare BM25, vector and hybrid retrieval using nDCG@10 and Recall@100.
4. Add checkpointed bulk ingestion and interruption/replay tests.
5. Benchmark p50/p95/p99, QPS, errors, index size and memory with explicit hardware/concurrency.
6. Add a bounded RDF/SPARQL tool and a small versioned knowledge-graph extract.
7. Exercise multi-node failure/recovery before making resilience claims.

## References

- https://docs.vespa.ai/en/learn/tutorials/hybrid-search.html
- https://github.com/modelcontextprotocol/python-sdk
- https://modelcontextprotocol.io/docs/learn/server-concepts

See [candidate evidence checklist](docs/candidate-evidence.md) for what to show and what remains unverified.
