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
from app.observability import QueryMeasurements, IngestionMeasurements, current
from app.rankings import Ranking, catalog as ranking_catalog

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
query_measurements = QueryMeasurements()
ingestion_measurements = IngestionMeasurements()


def bounded_search(query, limit, ranking="lexical", source="all"):
    observation = current.get()
    if observation is not None:
        observation.ranking = ranking
    if not slots.acquire(blocking=False):
        raise SearchUnavailable("Search concurrency limit reached; retry later", 'busy')
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
    ranking: Ranking = "lexical",
    source: SourceFilter = "all",
) -> dict:
    """Search indexed cards, Wikipedia introductions and bounded structured sources with optional source filter."""
    with query_measurements.measure('search', 'mcp', 'vespa'):
        return bounded_search(query, limit, ranking, source)


@mcp.tool()
def get_document(document_id: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,64}$")]) -> dict:
    """Read one document and its provenance from the indexed corpus."""
    with query_measurements.measure('document', 'mcp', 'vespa') as observation:
        document = backend.get_document(document_id)
        if document is None:
            observation.outcome = 'not_found'
            raise ValueError("Document not found")
        return document


@mcp.tool()
def query_index(scenario: lab.YQLScenario = "search", query: Annotated[str, Field(min_length=1,max_length=256)] = "réplication", source: SourceFilter = "all", ranking: Ranking = "lexical", limit: Annotated[int,Field(ge=1,le=20)] = 5) -> dict:
    """Execute approved YQL templates; return text, bound parameters and real Vespa results."""
    with query_measurements.measure('lab', 'mcp', 'vespa'):
        return lab.execute(lab.LabRequest(engine="yql",scenario=scenario,query=query,source=source,ranking=ranking,limit=limit),backend)


@mcp.tool()
def query_graph(scenario: lab.GraphScenario = "relations", entity: Annotated[str,Field(pattern=r"^Q[1-9][0-9]{0,10}$")] = "Q11660", language: Literal["fr","en"] = "fr", limit: Annotated[int,Field(ge=1,le=20)] = 5) -> dict:
    """Query the local versioned Wikidata graph with approved SELECT templates and bounded paths."""
    with query_measurements.measure('lab', 'mcp', 'sparql'):
        return lab.execute(lab.LabRequest(engine="sparql",scenario=scenario,entity=entity,language=language,limit=limit),backend)


@asynccontextmanager
async def lifespan(app):
    stop = threading.Event()
    def sample_ingestion():
        while not stop.is_set():
            try:
                ingestion_measurements.sample(snapshot(), sources_status())
            except (sqlite3.Error, OSError, ValueError):
                # Status failures remain unavailable; never invent a zero sample.
                pass
            stop.wait(15)
    sampler = threading.Thread(target=sample_ingestion, daemon=True, name='ingestion-metrics')
    sampler.start()
    async with mcp.session_manager.run():
        try:
            yield
        finally:
            stop.set()
            sampler.join(timeout=4)
    backend.client.close()


app = FastAPI(title="Laboratoire de recherche", description="API de recherche lexicale et de consultation des fiches pédagogiques.", lifespan=lifespan)


@app.middleware('http')
async def observe_queries(request: Request, call_next):
    path = request.url.path
    operation = ('search' if path == '/api/search' else
        'lab' if path == '/api/lab/execute' else
        'document' if path.startswith('/api/documents/') else None)
    if operation is None:
        return await call_next(request)
    with query_measurements.measure(operation, 'http', 'unvalidated' if operation == 'lab' else 'vespa') as observation:
        response = await call_next(request)
        if response.status_code == 422:
            observation.outcome = 'rejected'
        elif response.status_code == 404:
            observation.outcome = 'not_found'
        elif response.status_code >= 500:
            observation.outcome = 'busy' if observation.reason == 'busy' else 'error'
        response.headers['X-Request-ID'] = observation.identifier
        return response


@app.exception_handler(SearchUnavailable)
async def unavailable(request: Request, exc):
    observation = current.get()
    if observation is not None:
        observation.reason = exc.reason
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/", response_class=HTMLResponse)
def home():
    return Path(__file__).with_name("index.html").read_text()


@app.get("/lab", response_class=HTMLResponse)
def laboratory():
    return Path(__file__).with_name("lab.html").read_text()


@app.get('/observability', response_class=HTMLResponse)
def observability_page():
    return Path(__file__).with_name('observability.html').read_text()


@app.get('/benchmark', response_class=HTMLResponse)
def benchmark_page():
    return Path(__file__).with_name('benchmark.html').read_text()


@app.get('/api/observability')
def observability():
    return {'generated_at': time.time(), 'queries': query_measurements.snapshot(),
        'wikipedia': snapshot(), 'sources': sources_status(),
        'ingestion': ingestion_measurements.snapshot()}


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
           ranking: Ranking = "lexical",
           source: SourceFilter = "all"):
    if not q.strip():
        raise HTTPException(422, "Query cannot be blank")
    return bounded_search(q, limit, ranking, source)


@app.get('/api/rankings')
def rankings():
    return ranking_catalog()


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
    observation = current.get()
    if observation is not None:
        observation.engine = 'vespa' if request.engine == 'yql' else 'sparql'
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
        search_counters = dict(metrics)
    lines = [f"poc_search_{key} {value}" for key, value in search_counters.items()]
    queries = query_measurements.snapshot()
    lines += ['# TYPE poc_query_completed_total counter',
        f'poc_query_completed_total {queries["totals"]["completed"]}',
        '# TYPE poc_query_in_flight gauge',
        f'poc_query_in_flight {queries["totals"]["in_flight"]}',
        '# TYPE poc_query_duration_seconds_sum counter',
        f'poc_query_duration_seconds_sum {queries["totals"]["duration_seconds"]}',
        '# TYPE poc_query_outcome_total counter']
    lines += [f'poc_query_outcome_total{{outcome="{key}"}} {value}'
        for key, value in queries['totals']['outcomes'].items()]
    wiki, structured = snapshot(), sources_status()
    lines += ['# TYPE poc_ingestion_registry_available gauge',
        '# TYPE poc_ingestion_pending gauge', '# TYPE poc_ingestion_indexed gauge',
        '# TYPE poc_ingestion_writes_total counter', '# TYPE poc_ingestion_errors_total counter']
    for name, data in [('wikipedia', wiki), ('structured', structured)]:
        available = data['state'] not in ('waiting', 'unavailable')
        lines.append(f'poc_ingestion_registry_available{{collector="{name}"}} {int(available)}')
        if available:
            for field in ('writes', 'errors'):
                if data.get(field) is not None:
                    lines.append(f'poc_ingestion_{field}_total{{collector="{name}"}} {data[field]}')
    if wiki['state'] not in ('waiting', 'unavailable'):
        lines += [f'poc_ingestion_pending{{collector="wikipedia"}} {wiki["pending"]}',
            f'poc_ingestion_indexed{{collector="wikipedia"}} {wiki["indexed"]}']
    if structured['state'] == 'available':
        for source in structured['sources']:
            lines.append(f'poc_ingestion_pending{{collector="{source["source"]}"}} {source["pending_documents"]}')
            if source['last_success']:
                lines.append(f'poc_ingestion_last_success_seconds{{collector="{source["source"]}"}} {source["last_success"]}')
    return '\n'.join(lines) + '\n'


app.mount("/tools", mcp.streamable_http_app())
