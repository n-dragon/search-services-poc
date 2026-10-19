"""Deploy the schema, then replay a small corpus using stable IDs."""
import io
import os
from pathlib import Path
import time
import zipfile
import httpx
from scripts.corpus import CARDS


def retry(operation, attempts=60):
    for attempt in range(attempts):
        try:
            return operation()
        except httpx.HTTPError:
            if attempt == attempts - 1:
                raise
            time.sleep(2)


def main():
    package = io.BytesIO()
    root = Path(__file__).resolve().parents[1] / "vespa"
    with zipfile.ZipFile(package, "w") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root))
    with httpx.Client(timeout=60, trust_env=False) as client:
        config = os.getenv("VESPA_CONFIG_URL", "http://localhost:19071")
        deploy = retry(lambda: client.post(config + "/application/v2/tenant/default/prepareandactivate",
                                           content=package.getvalue(),
                                           headers={"Content-Type": "application/zip"}).raise_for_status())
        print("Application deployed", deploy.status_code)
        base = os.getenv("VESPA_URL", "http://localhost:8080")
        for identifier, title, body, source in CARDS:
            fields = {"card_id": identifier, "title": title, "body": body, "source": source}
            retry(lambda: client.post(f"{base}/document/v1/poc/card/docid/{identifier}", json={"fields": fields}).raise_for_status())
        def searchable():
            result = client.get(base + "/search/", params={"yql": "select * from sources card where true", "hits": 0})
            result.raise_for_status()
            payload = result.json()
            if payload.get("root", {}).get("errors") or payload.get("root", {}).get("fields", {}).get("totalCount") != len(CARDS):
                raise httpx.HTTPError("Corpus not searchable yet")
        retry(searchable)
        print(f"Ready: {len(CARDS)} original documents; stable IDs, replay-safe writes")


if __name__ == "__main__":
    main()
