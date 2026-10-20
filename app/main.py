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
from typing import Annotated, Literal
from app.backend import VespaBackend, SearchUnavailable, CORPUS_VERSION
from app.analytics import Event, record
from app.ingestion import snapshot
from app.sources import status as sources_status
from app import lab

backend = VespaBackend()
mcp = FastMCP("Search Services POC", stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["localhost", "127.0.0.1", "localhost:*", "127.0.0.1:*", *filter(None, os.getenv("MCP_ALLOWED_HOSTS", "").split(","))],
        allowed_origins=["http://localhost:*", *filter(None, os.getenv("MCP_ALLOWED_ORIGINS", "").split(","))],
    ))
SourceFilter = Literal["all", "wikipedia-fr", "systems-cards-fr-v2", "wikidata", "uniprot", "materials", "openalex", "openaire", "eurostat", "worldbank", "imf", "oecd", "datagouv"]
slots = threading.BoundedSemaphore(8)
metrics_lock = threading.Lock()
metrics = {"requests": 0, "errors": 0, "duration_seconds": 0.0}


def bounded_search(query, limit, ranking="lexical", source="all"):
    if not slots.acquire(blocking=False):
        raise SearchUnavailable("Search concurrency limit reached; retry later")
    start = time.perf_counter()
    failed = False
    try:
        return backend.search(query, limit, ranking, source)
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
    ranking: Literal["lexical", "lexical-balanced"] = "lexical",
    source: SourceFilter = "all",
) -> dict:
    """Search indexed cards, Wikipedia introductions and bounded structured sources with optional source filter."""
    return bounded_search(query, limit, ranking, source)


@mcp.tool()
def get_document(document_id: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,64}$")]) -> dict:
    """Read one document and its provenance from the indexed corpus."""
    document = backend.get_document(document_id)
    if document is None:
        raise ValueError("Document not found")
    return document


@mcp.tool()
def query_index(scenario: lab.YQLScenario = "search", query: Annotated[str, Field(min_length=1,max_length=256)] = "réplication", source: SourceFilter = "all", ranking: Literal["lexical","lexical-balanced"] = "lexical", limit: Annotated[int,Field(ge=1,le=20)] = 5) -> dict:
    """Execute approved YQL templates; return text, bound parameters and real Vespa results."""
    return lab.execute(lab.LabRequest(engine="yql",scenario=scenario,query=query,source=source,ranking=ranking,limit=limit),backend)


@mcp.tool()
def query_graph(scenario: lab.GraphScenario = "relations", entity: Annotated[str,Field(pattern=r"^Q[1-9][0-9]{0,10}$")] = "Q11660", language: Literal["fr","en"] = "fr", limit: Annotated[int,Field(ge=1,le=20)] = 5) -> dict:
    """Query the local versioned Wikidata graph with approved SELECT templates and bounded paths."""
    return lab.execute(lab.LabRequest(engine="sparql",scenario=scenario,entity=entity,language=language,limit=limit),backend)


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield
    backend.client.close()


app = FastAPI(title="Laboratoire de recherche", description="API de recherche lexicale et de consultation des fiches pédagogiques.", lifespan=lifespan)


@app.exception_handler(SearchUnavailable)
async def unavailable(request: Request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/", response_class=HTMLResponse)
def home():
    return Path(__file__).with_name("index.html").read_text()


@app.get("/lab", response_class=HTMLResponse)
def laboratory():
    return Path(__file__).with_name("lab.html").read_text()


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


@app.get("/api/ingestion")
def ingestion():
    return snapshot()


@app.get("/api/sources")
def sources():
    return sources_status()


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=256), limit: int = Query(default=5, ge=1, le=10),
           ranking: Literal["lexical", "lexical-balanced"] = "lexical",
           source: SourceFilter = "all"):
    if not q.strip():
        raise HTTPException(422, "Query cannot be blank")
    return bounded_search(q, limit, ranking, source)


@app.get("/api/lab/catalog")
def lab_catalog():
    return lab.catalog()


@app.post("/api/lab/preview")
def lab_preview(request: lab.LabRequest):
    try:
        return lab.compile_query(request)
    except lab.Rejected as exc:
        raise HTTPException(422,str(exc)) from exc


@app.post("/api/lab/execute")
def lab_execute(request: lab.LabRequest):
    try:
        return lab.execute(request,backend)
    except lab.Rejected as exc:
        raise HTTPException(422,str(exc)) from exc


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
