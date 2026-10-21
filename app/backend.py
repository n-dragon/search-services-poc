"""Bounded, read-only Vespa queries shared by HTTP and MCP."""
import os
import re
import httpx
from app.sources import ALL_CORPORA
from app.observability import engine_span

RANKINGS = {"lexical", "lexical-balanced"}

CORPUS_VERSION = "multi-source-fr-v4"

class SearchUnavailable(Exception):
    def __init__(self, message, reason='unavailable'):
        super().__init__(message)
        self.reason = reason

class VespaBackend:
    def __init__(self, client=None):
        self.client = client or httpx.Client(
            base_url=os.getenv("VESPA_URL", "http://localhost:8080"),
            timeout=httpx.Timeout(5.0, connect=2.0),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            trust_env=False,
        )

    def _request(self, path, params=None):
        with engine_span():
            return self._perform_request(path, params)

    def _perform_request(self, path, params=None):
        try:
            response = self.client.get(path, params=params)
            response.raise_for_status()
            data = response.json()
            errors = data.get("root", {}).get("errors")
            if errors:
                # Vespa error code 12 denotes a query timeout. Never retain messages.
                reason = 'timeout' if any(e.get('code') == 12 for e in errors) else 'unavailable'
                raise SearchUnavailable("Vespa reported a query error", reason)
            return data
        except httpx.TimeoutException as exc:
            raise SearchUnavailable("Search backend timeout", 'timeout') from exc
        except httpx.HTTPStatusError as exc:
            reason = 'timeout' if exc.response.status_code == 504 else 'unavailable'
            raise SearchUnavailable("Search backend unavailable", reason) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise SearchUnavailable("Search backend unavailable") from exc

    def search(self, query: str, limit: int = 5, ranking: str = "lexical", source: str = "all"):
        if ranking not in RANKINGS:
            raise ValueError("Unknown ranking profile")
        if source not in ALL_CORPORA:
            raise ValueError("Unknown source filter")
        query = query.strip()
        if not 1 <= len(query) <= 256 or not 1 <= limit <= 10:
            raise ValueError("query must have 1–256 characters; limit must be 1–10")
        data = self._request("/search/", {
            "yql": "select * from sources card where userInput(@query)" + (" and corpus contains @corpus" if source != "all" else ""),
            **({"corpus": source} if source != "all" else {}),
            "query": query, "type": "any", "ranking": ranking, "language": "fr",
            "hits": limit, "timeout": "2s",
        })
        hits = [{"id": h["fields"]["card_id"], "title": h["fields"]["title"],
                 "body": h["fields"]["body"][:400], "source": h["fields"]["source"],
                 **{key: h["fields"].get(key, "") for key in ("corpus", "revision", "indexed_at", "license", "history", "native_id", "kind", "source_updated", "fetched_at")},
                 "features": h["fields"].get("summaryfeatures", {}), "score": h.get("relevance", 0)}
                for h in data.get("root", {}).get("children", []) if "fields" in h]
        return {"backend": "vespa", "ranking": "BM25", "ranking_profile": ranking, "source_filter": source, "corpus_version": CORPUS_VERSION,
                "total": data.get("root", {}).get("fields", {}).get("totalCount", 0), "hits": hits}

    def get_document(self, document_id: str):
        if not re.fullmatch(r"[a-z0-9-]{1,64}", document_id):
            raise ValueError("Invalid document identifier")
        try:
            data = self._request(f"/document/v1/poc/card/docid/{document_id}")
        except SearchUnavailable as exc:
            if isinstance(exc.__cause__, httpx.HTTPStatusError) and exc.__cause__.response.status_code == 404:
                return None
            raise
        return {**data.get("fields", {}), "corpus_version": CORPUS_VERSION}

    def ready(self):
        self._request("/search/", {"yql": "select * from sources card where true", "hits": 0})
        return True
