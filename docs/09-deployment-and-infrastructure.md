# 09 — Deployment, Infrastructure & DevOps

Covers §38–§41.

> **Cost figures below are indicative list prices as understood at time of writing and must be
> re-checked against current provider pricing before you commit.** The comparison's *shape* — the
> 4–6x gap between Option A and Option B, and where the operational burden sits — is stable; the
> exact monthly numbers are not.

---

## 1. Option comparison

### Option A — Hetzner + Cloudflare (recommended for Stage 1)

| Component | Spec | ~USD/mo |
|---|---|---|
| App server (API + worker + Redis) | CPX31 — 4 vCPU, 8 GB, 160 GB | 16 |
| Database server | CX42 — 8 vCPU, 16 GB, 160 GB NVMe, PostgreSQL 16 + PostGIS | 30 |
| Object storage | Cloudflare R2, ~100 GB + zero egress fees | 2 |
| CDN + DNS + WAF | Cloudflare Free/Pro | 0–20 |
| Frontend hosting | Cloudflare Pages | 0 |
| Backups | Hetzner volume + pgBackRest to R2 | 5 |
| Email | Resend / SES | 0–10 |
| Error tracking | Sentry team | 0–26 |
| **Total** | | **~55–110** |

**Strengths:** by far the cheapest capable option; R2's zero egress matters because delivery photos
are the fastest-growing data; plain VPS boxes mean no provider-specific lock-in.
**Weaknesses:** you operate PostgreSQL yourself — backups, upgrades, failover are your job.
Hetzner has no managed PostgreSQL. Regions are EU/US only, which is a real latency consideration if
the sites are in South Asia (≈120–180 ms RTT to Falkenstein from Karachi/Delhi). Mitigated because
the mobile app is offline-first and the web app is CDN-fronted, but it is not nothing.

### Option B — AWS

| Component | Spec | ~USD/mo |
|---|---|---|
| ECS Fargate (API 2 tasks + worker 1) | 1 vCPU / 2 GB each | 75 |
| RDS PostgreSQL | db.t4g.medium Multi-AZ, 100 GB gp3 | 145 |
| ElastiCache Redis | cache.t4g.micro | 15 |
| ALB | | 20 |
| S3 + CloudFront | 100 GB + egress | 20 |
| Route 53, Secrets Manager, CloudWatch | | 20 |
| **Total** | | **~295** (single-AZ RDS: ~215) |

**Strengths:** managed PostgreSQL with automated backups, PITR and failover; ap-south-1 is close to
the likely user base; IAM, KMS and audit tooling matter if an enterprise customer or lender audits
you; horizontal scaling is a config change.
**Weaknesses:** 4–5x the cost at this size, and the cost curve is steep and surprising (NAT
gateway, egress, CloudWatch ingestion). Real operational complexity you will pay for in engineer
hours before you pay for it in dollars.

### Option C — DigitalOcean

| Component | Spec | ~USD/mo |
|---|---|---|
| App Platform or 2 Droplets | 4 GB / 2 vCPU | 48 |
| Managed PostgreSQL | 2 vCPU / 4 GB, with PostGIS | 60 |
| Managed Redis (Valkey) | 1 GB | 15 |
| Spaces + CDN | 250 GB | 5 |
| Load balancer | | 12 |
| **Total** | | **~140** |

**Strengths:** the honest middle. Managed PostgreSQL with PostGIS and daily backups without AWS's
surface area; Bangalore region; pricing you can predict. **Weaknesses:** fewer knobs than AWS
(no Multi-AZ PostgreSQL failover at the low tier, read replicas are a separate spend); smaller
ecosystem if you later want managed Kubernetes at scale.

### Recommendation

**Start on Option A, architect for B.** The application is deliberately provider-agnostic — Docker
images, S3-protocol object storage, plain PostgreSQL and Redis, no proprietary services. The entire
migration path is: restore a `pg_dump`/pgBackRest backup into RDS, repoint `DATABASE_URL`, `sync`
the R2 bucket to S3, and deploy the same image to ECS. Budget one weekend, not one quarter.

**Unless**: if the business will be audited by a bank or a government client inside 12 months, or if
sub-100 ms latency to South Asia is a stated requirement, start on **Option C in Bangalore** and
skip the migration. The extra ~$85/month is cheaper than doing the move under deadline.

---

## 2. Deployment evolution (§40)

| Stage | Trigger | Shape |
|---|---|---|
| **1** | Launch, < 50 users | One VPS: `docker compose` runs api, worker, redis, nginx. PostgreSQL on a second VPS. Objects in R2. Frontend on Pages. **No Kubernetes.** |
| **2** | Sustained CPU > 60%, or DB contention | Separate api / worker hosts. PostgreSQL tuned and moved to dedicated NVMe. Redis its own instance. PgBouncer in front of PostgreSQL. |
| **3** | > 500 concurrent, or availability requirement | 2+ API instances behind a load balancer (stateless already — sessions are JWT, cache is Redis). Blue-green deploys. Dedicated Celery workers per queue class. |
| **4** | Ops burden exceeds the cost delta | Managed PostgreSQL (RDS/DO) with PITR, managed Redis, container orchestration (ECS/App Platform). |
| **5** | Multi-region, or a genuinely independent workload | Kubernetes, read replicas for reporting, extraction of the first service — **reporting/exports first**, because it is read-only and has the most divergent scaling profile. The modular monolith's module boundaries are already the seams. |

---

## 3. Containers

`docker-compose.yml` (development):

```yaml
services:
  db:      postgis/postgis:16-3.4      # ports 5432, healthcheck pg_isready
  redis:   redis:7-alpine              # appendonly yes
  minio:   minio/minio                 # S3-compatible local object store
  api:     build ./backend             # uvicorn --reload, mounts source
  worker:  build ./backend             # celery -A app.workers worker -B
  web:     build ./web                 # vite dev server
  mailhog: axllent/mailpit             # catches outbound email locally
```

`docker-compose.prod.yml`: no source mounts, `gunicorn -k uvicorn.workers.UvicornWorker -w 4`,
resource limits, `restart: unless-stopped`, log driver with rotation, and healthchecks wired to the
deploy script.

Backend `Dockerfile` is multi-stage (builder installs with `uv` into a venv; runtime is
`python:3.12-slim`, non-root user, no build toolchain). Target image < 250 MB.

---

## 4. Configuration

All configuration is environment variables, validated at startup by `Settings` — the process
refuses to boot on a missing or malformed secret rather than failing at 3 a.m. on first use.
`.env.example` is committed and exhaustive; `.env` never is. Production secrets live in the
deployment target's secret store (Docker secrets at Stage 1; SSM/Secrets Manager at Stage 4) and
are never baked into an image.

---

## 5. Database operations

| Concern | Approach |
|---|---|
| Migrations | Alembic, one head, autogenerate **reviewed by hand** every time. Forward-only in production. |
| Migration safety | No table rewrites on hot tables; `CREATE INDEX CONCURRENTLY` (outside the transaction); expand-contract for column changes; every migration reversible or explicitly documented as not. |
| Deploy order | Migrate → deploy → (later release) drop old columns. Code must tolerate both schema versions for one release. |
| Backups | pgBackRest: full weekly, differential daily, WAL archived continuously to R2. RPO 5 min. |
| **Restore drills** | A scheduled monthly job restores the latest backup into a scratch instance and runs a row-count and trial-balance check. **A backup that has never been restored is not a backup.** |
| Retention | 30 days PITR, 12 monthly fulls, 7 yearly for financial records. |
| Connection pooling | PgBouncer transaction mode; SQLAlchemy pool sized to `2 * cpu + 1` per worker. |
| Tuning | `shared_buffers` 25% RAM, `effective_cache_size` 60%, `work_mem` sized off actual sort/hash usage, `pg_stat_statements` on from day one. |

---

## 6. CI/CD (GitHub Actions, §41)

```
ci.yml        on: pull_request, push
  backend:  ruff check · ruff format --check · mypy --strict app/
            · import-linter · pytest (postgres+redis services) with coverage gate 80%
            · alembic upgrade head then `alembic check` (model/migration drift)
            · pip-audit
  frontend: tsc --noEmit · eslint · vitest --coverage · build
            · openapi drift check (regenerate types, fail if dirty)
  mobile:   tsc --noEmit · eslint · jest
  e2e:      docker compose up · seed · playwright test · upload traces on failure
  security: gitleaks · trivy image scan · npm audit

deploy.yml    on: push to main (after ci), or manual dispatch
  build and push images to GHCR (tagged with the commit sha)
  ssh to host → pull → `alembic upgrade head` → rolling restart → healthcheck
  → automatic rollback to the previous image tag if /ready fails within 60 s
  → Sentry release + source maps
```

Branch protection on `main`: CI green, one review, no force-push. Migrations run as a distinct,
logged step so a failed migration is never mistaken for a failed deploy.

---

## 7. Observability

| Signal | Tool |
|---|---|
| Logs | `structlog` JSON to stdout → Loki (or the provider's log sink). Every line carries `request_id`, `user_id`, `company_id`. |
| Errors | Sentry, backend + web + mobile, with release tagging and PII scrubbing |
| Metrics | `prometheus-fastapi-instrumentator` → Prometheus → Grafana. RED metrics per endpoint, Celery queue depth and age, DB pool saturation, outbox lag, **sync-push failure rate**. |
| Tracing | OpenTelemetry, sampled 5%, on from Stage 2 |
| Uptime | External probe on `/ready` from two regions |
| Business alarms | Outbox lag > 5 min · approval SLA breaches > N · inventory balance vs ledger divergence · failed sync pushes > 1% · any unbalanced journal entry (should be structurally impossible — alert if it ever fires) |

## 8. Security posture in production

TLS 1.2+ via Let's Encrypt with auto-renewal · HSTS, CSP, `X-Content-Type-Options`,
`Referrer-Policy`, `Permissions-Policy` · CORS locked to known origins, no wildcard ·
PostgreSQL bound to the private network only, never a public IP · SSH key-only, root login
disabled, fail2ban · UFW default-deny · unattended security upgrades · the application's database
role holds no DDL rights and no UPDATE/DELETE on the append-only tables · uploads validated by
magic bytes not extension, size-capped, stored outside the web root with randomised keys and served
through short-lived presigned URLs · secrets rotated on personnel change.

---

## 9. Local development

```
cp .env.example .env
make up            # docker compose up -d
make migrate       # alembic upgrade head
make seed          # realistic demo data (§43)
make test          # backend + frontend
make e2e           # playwright
```

Backend at `:8000`, web at `:5173`, MinIO console at `:9001`, Mailpit at `:8025`.
Mobile: `cd mobile && npm install && npx expo start` — note the Android emulator reaches the host
API at `http://10.0.2.2:8000`, not `localhost` (carried over from the prototype's README, which had
this right).
