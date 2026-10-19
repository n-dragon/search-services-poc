# Deploy a candidate demo

For the supplied Ubuntu VM (4 GB), use [GitHub Actions deployment](github-actions-deployment.md), which applies the small-corpus memory profile. The manual HTTPS instructions below describe the general deployment.

## Prerequisites

- A Linux VM with Docker Engine and the Compose plugin, starting with at least 6 GB RAM and 20 GB free disk. Measure actual resource use before extending the corpus.
- A domain pointing to the VM (correct both A and AAAA records).
- Inbound 80/443 for Caddy certificate issuance and HTTPS. Restrict SSH to your administrative access.
- No external ports for Vespa, its configuration service, or document ingestion.

An Ubuntu VM with 4 GB RAM has been supplied. SSH access still needs configuration. No host has been changed and no paid resource has been provisioned.

## Steps

1. Clone the repository onto the VM and enter the project directory.
2. Copy `.env.example` to `.env` and replace `DEMO_DOMAIN` with the real hostname.
3. Start the stack:

```sh
docker compose -f compose.yaml -f compose.online.yaml up -d --build
```

4. Check the local service with `python3 scripts/smoke.py`.
5. From another machine, check the actual HTTPS endpoint:

```sh
POC_URL=https://YOUR-ACTUAL-DOMAIN python3 scripts/smoke.py
```

6. Inspect the UI, verify one search and connect an MCP client to `/tools/mcp`.
7. Record the domain, tested commit, date and resource use in the README. Only then use the URL in a candidature.

This demo serves only public sample data. It has no authentication, global per-IP quotas or DDoS protection. Search concurrency is bounded but the stack is not hardened for arbitrary public traffic. Add edge rate limits if needed, monitor resources and keep the exposed tools read-only.

## Recovery

Inspect `docker compose logs --tail=150 vespa bootstrap web proxy` with the same two `-f` flags when using the online stack. Readiness fails if Vespa cannot answer a query. Restart the failed service, then repeat the smoke test. Persistent data survives container recreation; the original corpus can be replayed from source. This does not demonstrate replica failover.

## Cost

No provider or paid plan has been selected. Confirm VM capacity, domain and any ongoing cost before using a paid service. A temporary development URL is not a durable candidature link.
