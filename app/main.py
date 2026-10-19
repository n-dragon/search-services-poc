"""Web UI, HTTP API and official MCP SDK transport."""
from contextlib import asynccontextmanager
from pathlib import Path
import threading
import os
import time
import sqlite3
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from typing import Annotated
from app.backend import VespaBackend, SearchUnavailable, CORPUS_VERSION
from app.analytics import Event, record

backend = VespaBackend()
mcp = FastMCP("Search Services POC", stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["localhost", "127.0.0.1", "localhost:*", "127.0.0.1:*", *filter(None, os.getenv("MCP_ALLOWED_HOSTS", "").split(","))],
        allowed_origins=["http://localhost:*", *filter(None, os.getenv("MCP_ALLOWED_ORIGINS", "").split(","))],
    ))
slots = threading.BoundedSemaphore(8)
metrics_lock = threading.Lock()
metrics = {"requests": 0, "errors": 0, "duration_seconds": 0.0}


def bounded_search(query, limit):
    if not slots.acquire(blocking=False):
        raise SearchUnavailable("Search concurrency limit reached; retry later")
    start = time.perf_counter()
    failed = False
    try:
        return backend.search(query, limit)
    except Exception:
        failed = True
        raise
    finally:
        slots.release()
        with metrics_lock:
            metrics["requests"] += 1
            metrics["errors"] += int(failed)
            metrics["duration_seconds"] += time.perf_counter() - start


@mcp.tool()
def search_documents(
    query: Annotated[str, Field(min_length=1, max_length=256)],
    limit: Annotated[int, Field(ge=1, le=10)] = 5,
) -> dict:
    """Search the versioned systems corpus using Vespa BM25; return source links."""
    return bounded_search(query, limit)


@mcp.tool()
def get_document(document_id: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,64}$")]) -> dict:
    """Read one document and its provenance from the indexed corpus."""
    document = backend.get_document(document_id)
    if document is None:
        raise ValueError("Document not found")
    return document


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield
    backend.client.close()


app = FastAPI(title="Search Services POC", lifespan=lifespan)


@app.exception_handler(SearchUnavailable)
async def unavailable(request: Request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/", response_class=HTMLResponse)
def home():
    return Path(__file__).with_name("index.html").read_text()


@app.post("/api/events", status_code=204, include_in_schema=False)
def analytics_event(event: Event):
    try:
        record(event)
    except (sqlite3.Error, OSError):
        raise HTTPException(503, "Analytics temporarily unavailable")


@app.get("/healthz")
def live():
    return {"status": "alive"}


@app.get("/readyz")
def ready():
    backend.ready()
    return {"status": "ready", "backend": "vespa", "corpus_version": CORPUS_VERSION}


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=256), limit: int = Query(default=5, ge=1, le=10)):
    if not q.strip():
        raise HTTPException(422, "Query cannot be blank")
    return bounded_search(q, limit)


@app.get("/api/documents/{document_id}")
def document(document_id: str):
    try:
        result = backend.get_document(document_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Document not found")
    return result


@app.get("/metrics", response_class=PlainTextResponse)
def get_metrics():
    with metrics_lock:
        snapshot = dict(metrics)
    return "\n".join(f"poc_search_{key} {value}" for key, value in snapshot.items()) + "\n"


app.mount("/tools", mcp.streamable_http_app())
