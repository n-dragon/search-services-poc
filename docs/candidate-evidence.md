# Evidence for a candidature

## Current status

- Source implemented for lexical search, API, UI and official MCP SDK.
- API/MCP tests run locally against mocked Vespa transport; see the test report delivered with this source.
- Real Vespa startup, schema deployment, ingestion, a BM25 query, document provenance and MCP discovery passed in GitHub Actions on commit `f013e39cbf765a4f6145812856ab83a8cf9ccfb1` (19 October 2026).
- GitHub repository published: https://github.com/n-dragon/search-services-poc
- GitHub Actions API/MCP test job passed on the initial implementation commit.
- Both CI jobs passed: https://github.com/n-dragon/search-services-poc/actions/runs/39252822478
- Initial online deployment and public smoke checks passed on commit `fb92ba9f7fff1333d647d8840f97df6cb4f83489`; current UI/analytics changes require a new deployment.

## Release checklist

- [x] GitHub repository created and source pushed
- [x] Both CI jobs green on the initial implementation commit
- [ ] HTTPS instance running on a stable domain (initial HTTP VM deployment is working)
- [ ] Remote smoke test and manual UI review complete
- [ ] Real MCP client tested remotely
- [ ] README includes live URL, tested commit and date
- [ ] Measured resource use recorded

## Narrative after verification

Explain why Vespa, where query limits are enforced, how MCP and HTTP share retrieval behavior, and how source/version provenance is preserved. Show one actual failure and recovery. Use only measured results. Do not call a ten-document single-node integration demo a web-scale platform.
