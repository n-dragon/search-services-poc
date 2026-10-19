# Evidence for a candidature

## Current status

- Source implemented for lexical search, API, UI and official MCP SDK.
- API/MCP tests run locally against mocked Vespa transport; see the test report delivered with this source.
- Real Vespa startup, schema deployment, ingestion and ranking have not been executed in the authoring environment (Docker/Podman unavailable).
- GitHub repository publication, Actions result and online instance remain pending.

## Release checklist

- [ ] GitHub repository created and source pushed
- [ ] Both CI jobs green on the published commit
- [ ] HTTPS instance running on a stable domain
- [ ] Remote smoke test and manual UI review complete
- [ ] Real MCP client tested remotely
- [ ] README includes live URL, tested commit and date
- [ ] Measured resource use recorded

## Narrative after verification

Explain why Vespa, where query limits are enforced, how MCP and HTTP share retrieval behavior, and how source/version provenance is preserved. Show one actual failure and recovery. Use only measured results. Do not call a ten-document single-node integration demo a web-scale platform.
