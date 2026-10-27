# Besu Network Monitor

Public, read-only Hyperledger Besu/QBFT Network Monitor (**V1.0.0**).

Production dashboard: `https://monitor.dimikog.org`
Production API: `https://api.monitor.dimikog.org`

The trusted collector/publisher pipeline is operational. The Monitor API provides
service health, overview, consensus (snapshot), infrastructure (snapshot), and
live chain (public JSON-RPC). A static Next.js frontend under `frontend/` is
deployed via GitHub Actions to GitHub Pages.

## Architecture

| Role | Host | Responsibility |
|---|---|---|
| Trusted collector/publisher | Existing Prometheus monitoring VM | Consensus via SSH+localhost Besu RPC; infrastructure via local Prometheus API; atomically publish sanitized snapshots to my-backend |
| Public API | Separate `my-backend` VM | Serve read-only Monitor API; **must not** SSH into validators or infrastructure; **must not** require Prometheus credentials |
| Frontend | GitHub Pages (`frontend/`) | Static Next.js export; polls public Monitor API only |

**Trusted publish flow**

```text
Prometheus VM
  |-- collect_consensus.py      -> sanitized consensus_snapshot.json
  |-- collect_infrastructure.py -> sanitized infrastructure_snapshot.json
  |-- dedicated publisher SSH (~/.ssh/besu_monitor_publisher)
  v
my-backend:/var/lib/besu-monitor/current/
  |-- consensus_snapshot.json
  |-- infrastructure_snapshot.json
```

Raw Prometheus data is never published. The Internet-facing API only reads
already-sanitized snapshot files on disk.

**Live vs collected data**

| Data | Source | Endpoint |
|---|---|---|
| Consensus / QBFT telemetry | Trusted collector snapshot on `my-backend` | `GET /api/v1/consensus` |
| Infrastructure availability | Trusted collector snapshot on `my-backend` | `GET /api/v1/infrastructure` |
| Chain head / recent blocks | Live public Besu JSON-RPC (FastAPI → RPC) | `GET /api/v1/chain` |

Internal consensus and infrastructure telemetry arrive as sanitized snapshots
from the trusted Prometheus VM publisher. Live chain metrics are queried by the
API from the public RPC only (default Edu-Net: `https://rpc.dimikog.org/rpc/`).
Raw internal reports, validator RPC, SSH, Prometheus credentials/labels, and
`qbft_*` methods must never be exposed publicly.

Cadence: consensus and infrastructure pipelines every **5 minutes** via systemd
timers (unit files under `deploy/systemd/`; installed and enabled in the
reference V1 deployment). The API treats consensus and infrastructure telemetry
as **stale after 15 minutes** (900 seconds) by default. Chain health uses live
block age thresholds (15s / 30s by default).

Deployment-specific network addresses (validator SSH targets and the publisher
destination host) are **not** part of the repository configuration. The
reference deployment supplies them externally (see collector configuration
below).

---

## Public Monitor API (`api/`)

The API runs on `my-backend`. Snapshot endpoints read atomically published files:

| Endpoint | Snapshot path |
|---|---|
| `GET /api/v1/consensus` | `/var/lib/besu-monitor/current/consensus_snapshot.json` |
| `GET /api/v1/infrastructure` | `/var/lib/besu-monitor/current/infrastructure_snapshot.json` |

Chain queries the configured public JSON-RPC URL on each request.

It does **not** SSH to validators or infrastructure hosts, does **not** query
Prometheus, does not talk to the collector process, and does not depend on the
old PoC API directory on the server.

### Implemented endpoints

| Method | Path | Meaning |
|---|---|---|
| `GET` | `/api/v1/health` | **API service** liveness only |
| `GET` | `/api/v1/overview` | Compact dashboard composition of chain + consensus + infrastructure |
| `GET` | `/api/v1/consensus` | Public consensus health, freshness, and sanitized metrics (snapshot) |
| `GET` | `/api/v1/infrastructure` | Public infrastructure availability health, freshness, and sanitized metrics (snapshot) |
| `GET` | `/api/v1/chain` | Live chain health, RPC node signals, and recent blocks (JSON-RPC) |

### Overview endpoint (`GET /api/v1/overview`)

Compact landing-page composition. Reuses the same chain / consensus /
infrastructure collectors and evaluators as the domain endpoints — it does
**not** re-implement domain health rules, does **not** include `recent_blocks`,
and does **not** query Prometheus or QBFT.

**Overall health** (chain is primary):

| Chain | Consensus / Infrastructure | Overall |
|---|---|---|
| `critical` | any | `critical` |
| `unknown` | any | `unknown` |
| `degraded` | either `critical` | `critical` |
| `degraded` | otherwise | `degraded` |
| `healthy` | either `critical` | `critical` |
| `healthy` | either `degraded` (none critical) | `degraded` |
| `healthy` | otherwise (including secondary `unknown`) | `healthy` |

A secondary domain being `unknown` does **not** by itself downgrade a healthy
live chain. That situation is signaled separately:

- `observability: "complete"` — all three domain healths are known
- `observability: "partial"` — at least one domain health is `unknown`

Example: healthy chain + unknown consensus → `health=healthy`,
`observability=partial`.

```json
{
  "health": "healthy|degraded|critical|unknown",
  "observability": "complete|partial",
  "updated_at": "2026-10-26T12:00:00Z",
  "domains": {
    "chain": {
      "health": "healthy",
      "block_number": 0,
      "latest_block_age_seconds": 0,
      "average_block_time_seconds": 0
    },
    "consensus": {
      "health": "healthy",
      "freshness": "fresh|stale|unknown",
      "validators": 7,
      "synced": 7,
      "quorum": 5,
      "has_quorum": true
    },
    "infrastructure": {
      "health": "healthy",
      "freshness": "fresh|stale|unknown",
      "systems_up": 16,
      "systems_monitored": 16
    }
  }
}
```

Unavailable metrics are `null`. HTTP **200** for all valid health states.

### Service health vs monitored-network health

`GET /api/v1/health` answers only whether the FastAPI process is alive, for
example:

```json
{
  "status": "ok",
  "service": "besu-monitor-api"
}
```

It must **not** be interpreted as Besu network or consensus health.

Monitored consensus health is returned only by `GET /api/v1/consensus` under
the public vocabulary:

`healthy` | `degraded` | `critical` | `unknown`

### Source status vs public health

The collector snapshot field `status` is **source/collection** status:

`ok` | `warning` | `critical`

That value is **not** the public Monitor health vocabulary and is **not**
exposed as Monitor `health`.

For a **valid and fresh** snapshot the API maps:

| Collector `status` | Public `health` |
|---|---|
| `ok` | `healthy` |
| `warning` | `degraded` |
| `critical` | `critical` |

If the snapshot is missing, unreadable, malformed, schema-invalid, has an
invalid/naive/`generated_at` too far in the future, or is **stale**, public
consensus health is:

`unknown`

Stale telemetry is never reported as `healthy` merely because the stored source
status is `ok`. Freshness is evaluated from snapshot `generated_at` (not file
mtime).

### Why stale becomes `unknown`

Collection runs every 5 minutes. A 15-minute stale threshold allows roughly two
missed cycles before the API refuses to assert network health. Without a fresh
validated snapshot, the Monitor cannot honestly claim healthy/degraded/critical,
so it reports `unknown`. For **stale but still schema-valid** consensus and
infrastructure snapshots, last-known metrics are **retained** while `health`
becomes `unknown`. Missing/malformed/semantically invalid snapshots yield
`unknown` with **null** metrics.

### Consensus response shape

```json
{
  "health": "healthy|degraded|critical|unknown",
  "freshness": {
    "state": "fresh|stale|unknown",
    "age_seconds": 0,
    "stale_after_seconds": 900,
    "updated_at": "2026-10-26T12:00:00Z"
  },
  "consensus": {
    "reference_block": 0,
    "validator_count": 0,
    "synced_validators": 0,
    "quorum_required": 0,
    "has_quorum": true,
    "drift": {
      "observed_max_blocks": 0,
      "allowed_max_blocks": 2
    },
    "out_of_sync_count": 0
  }
}
```

When telemetry cannot be validated, `health` is `unknown` and consensus metric
fields may be `null`. HTTP status remains **200** for degraded/critical/stale/
unavailable telemetry. Reserve **5xx** for actual API failures that prevent
constructing this response.

### Infrastructure endpoint (`GET /api/v1/infrastructure`)

Provides public infrastructure availability health from the **sanitized trusted
snapshot** published by the Prometheus VM. `my-backend` does **not** query
Prometheus and does **not** SSH into infrastructure hosts.

This endpoint does **not** represent QBFT quorum or consensus health — that
remains `GET /api/v1/consensus`.

#### Infrastructure health semantics

The collector snapshot field `status` already uses the public vocabulary
(`healthy` | `degraded` | `critical` | `unknown`). For a **valid and fresh**
snapshot the API exposes that value as public `health` only after verifying it
matches the infrastructure policy implied by the metrics:

| Condition | Public `health` |
|---|---|
| All 16 systems UP | `healthy` |
| Primary RPC DOWN | `critical` |
| Any other system DOWN (primary RPC UP) | `degraded` |

Freshness uses the same 15-minute threshold and future-skew tolerance as
consensus (`BESU_MONITOR_STALE_AFTER_SECONDS`,
`BESU_MONITOR_FUTURE_TOLERANCE_SECONDS`):

| Snapshot state | `health` | Metrics |
|---|---|---|
| Valid + fresh | snapshot status (policy-checked) | present |
| Valid + stale | `unknown` | **retained** (last-known) |
| Missing / malformed / schema-invalid / contradictory / future beyond tolerance | `unknown` | **null** |

HTTP status remains **200** for healthy/degraded/critical/unknown.

#### Infrastructure response shape

```json
{
  "health": "healthy|degraded|critical|unknown",
  "freshness": {
    "state": "fresh|stale|unknown",
    "age_seconds": 0,
    "stale_after_seconds": 900,
    "updated_at": "2026-10-26T12:00:00Z"
  },
  "infrastructure": {
    "systems": {"monitored": 16, "up": 16, "down": 0},
    "validators": {"monitored": 7, "up": 7, "down": 0},
    "rpc": {"primary_up": true, "backup_up": true},
    "services": {
      "bootnode_up": true,
      "block_explorer_endpoint_up": true,
      "faucet_up": true,
      "identity_up": true,
      "backend_up": true,
      "prometheus_up": true,
      "grafana_endpoint_up": true
    }
  }
}
```

### Live chain endpoint (`GET /api/v1/chain`)

Provides operational chain health and a recent block window by querying the
**public** Besu JSON-RPC endpoint from the FastAPI process:

`Frontend → Monitor FastAPI → public Besu JSON-RPC`

Methods used: `eth_chainId`, `eth_blockNumber`, `eth_getBlockByNumber`,
`net_peerCount`, `eth_syncing`, `web3_clientVersion`. No `qbft_*` methods.

#### Chain health semantics

Based primarily on **latest block age** (wall clock vs block timestamp):

| Latest block age | Public `health` |
|---|---|
| ≤ `BESU_MONITOR_CHAIN_HEALTHY_AGE_SECONDS` (default 15s) | `healthy` |
| ≤ `BESU_MONITOR_CHAIN_CRITICAL_AGE_SECONDS` (default 30s) | `degraded` |
| > critical threshold | `critical` |
| RPC unavailable, malformed required data, wrong `chain_id`, or latest block materially in the future | `unknown` |

Small future clock skew is allowed using `BESU_MONITOR_FUTURE_TOLERANCE_SECONDS`
(same philosophy as consensus freshness).

Live chain ID must match `BESU_MONITOR_EXPECTED_CHAIN_ID`. A reachable endpoint
on the wrong chain is **not** reported healthy (`health=unknown`).

#### RPC node fields vs chain health

`rpc.peer_count` and `rpc.syncing` describe the **queried public RPC node**, not
the overall validator set or Monitor consensus health. `eth_syncing: false` means
that node reports itself not syncing; an object means it is syncing.

#### Recent block window

`BESU_MONITOR_RECENT_BLOCK_COUNT` (default 100) controls how many recent blocks
are retrieved (chunked JSON-RPC batches of at most
`BESU_MONITOR_RPC_BATCH_SIZE`, newest-first).

- `transactions_recent_blocks` — total txs in that window (not a hard-coded “last 100” name)
- `recent_block_count` — configured window size echoed in the response
- `interval_seconds` — seconds since the previous (older) block; **null on the oldest block** in the returned window
- `average_block_time_seconds` — mean of non-null adjacent intervals
- `gas_utilization_percent` — `gas_used / gas_limit * 100` (null if `gas_limit == 0`)

#### Chain response shape

```json
{
  "health": "healthy|degraded|critical|unknown",
  "updated_at": "2026-10-26T12:00:00Z",
  "chain": {
    "chain_id": 424242,
    "block_number": 0,
    "latest_block_age_seconds": 0,
    "average_block_time_seconds": 0,
    "recent_block_count": 100,
    "transactions_recent_blocks": 0,
    "average_gas_utilization_percent": 0
  },
  "rpc": {
    "reachable": true,
    "syncing": false,
    "peer_count": 0,
    "client_version": "besu/..."
  },
  "recent_blocks": []
}
```

On RPC failure or untrusted state: HTTP **200**, `health=unknown`, metrics null /
`recent_blocks` empty as appropriate. Raw RPC errors are never returned to clients.

### Browser CORS (V1)

Production API: `https://api.monitor.dimikog.org`  
Official frontend origin: `https://monitor.dimikog.org`

Default CORS policy (browser Origin allow-list only — **not** authentication or
API access control; curl/server clients remain unrestricted):

| Setting | Value |
|---|---|
| `allow_origins` | `https://monitor.dimikog.org` |
| `allow_methods` | `GET` |
| `allow_credentials` | `false` |

Override for local frontend development (example — do **not** ship localhost in
the production default):

```bash
export BESU_MONITOR_CORS_ORIGINS=https://monitor.dimikog.org,http://localhost:3000
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `BESU_MONITOR_CONSENSUS_SNAPSHOT` | `/var/lib/besu-monitor/current/consensus_snapshot.json` | Consensus snapshot path |
| `BESU_MONITOR_INFRASTRUCTURE_SNAPSHOT` | `/var/lib/besu-monitor/current/infrastructure_snapshot.json` | Infrastructure snapshot path |
| `BESU_MONITOR_STALE_AFTER_SECONDS` | `900` | Consensus/infrastructure stale-after age |
| `BESU_MONITOR_FUTURE_TOLERANCE_SECONDS` | `60` | Allowed future clock skew (snapshot `generated_at` and chain block timestamps) |
| `BESU_MONITOR_RPC_URL` | `https://rpc.dimikog.org/rpc/` | Public Besu JSON-RPC URL |
| `BESU_MONITOR_EXPECTED_CHAIN_ID` | `424242` | Expected `eth_chainId` |
| `BESU_MONITOR_RECENT_BLOCK_COUNT` | `100` | Recent block window size |
| `BESU_MONITOR_CHAIN_HEALTHY_AGE_SECONDS` | `15` | Latest-block age ≤ this → healthy |
| `BESU_MONITOR_CHAIN_CRITICAL_AGE_SECONDS` | `30` | Latest-block age ≤ this → degraded; above → critical |
| `BESU_MONITOR_RPC_TIMEOUT_SECONDS` | `10` | HTTP timeout for JSON-RPC calls |
| `BESU_MONITOR_RPC_BATCH_SIZE` | `25` | Max `eth_getBlockByNumber` calls per HTTP JSON-RPC batch (window is fetched in chunks) |
| `BESU_MONITOR_CORS_ORIGINS` | `https://monitor.dimikog.org` | Comma-separated browser Origin allow-list (CORS only; not authentication) |

### Local development

From the repository root (Python 3.10+):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt

# Optional local overrides:
export BESU_MONITOR_CONSENSUS_SNAPSHOT=/path/to/fixture/consensus_snapshot.json
export BESU_MONITOR_INFRASTRUCTURE_SNAPSHOT=/path/to/fixture/infrastructure_snapshot.json
export BESU_MONITOR_RPC_URL=https://rpc.dimikog.org/rpc/
export BESU_MONITOR_EXPECTED_CHAIN_ID=424242

uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
# or:
python -m api
```

### API tests

```bash
pip install -r api/requirements.txt
python3 -m unittest \
  tests.test_api_evaluation \
  tests.test_api_endpoints \
  tests.test_api_cors \
  tests.test_api_infrastructure \
  tests.test_api_overview \
  tests.test_api_chain \
  tests.test_collect_consensus \
  tests.test_collect_infrastructure \
  tests.test_run_consensus_pipeline \
  tests.test_run_infrastructure_pipeline \
  -v
```

Chain tests mock JSON-RPC and do **not** require Internet access.

### Production API systemd (`my-backend`)

Version-controlled unit file:

`deploy/systemd/besu-monitor-api.service`

Validated production layout on `my-backend`:

| Item | Value |
|---|---|
| Project directory | `/home/ubuntu/besu-network-monitor` |
| Virtualenv Python | `/home/ubuntu/besu-network-monitor/.venv/bin/python` |
| Bind | `127.0.0.1:8040` (localhost only; nginx provides public HTTPS) |
| Snapshots | `/var/lib/besu-monitor/current/{consensus,infrastructure}_snapshot.json` |

Application built-in defaults already match those snapshot paths, expected chain
ID `424242`, and the public RPC URL — the unit does **not** inject secrets or
redundant environment overrides.

#### Install / enable (manual on my-backend)

```bash
# 1) Install unit
sudo cp /home/ubuntu/besu-network-monitor/deploy/systemd/besu-monitor-api.service \
  /etc/systemd/system/besu-monitor-api.service
sudo systemctl daemon-reload

# 2) Enable and start
sudo systemctl enable --now besu-monitor-api.service

# 3) Status / logs / restart
sudo systemctl status besu-monitor-api.service --no-pager
sudo journalctl -u besu-monitor-api.service -n 100 --no-pager
sudo journalctl -u besu-monitor-api.service -f
sudo systemctl restart besu-monitor-api.service
```

Quick local check (on the VM, after start):

```bash
curl -sS http://127.0.0.1:8040/api/v1/health
curl -sS http://127.0.0.1:8040/api/v1/overview | python3 -m json.tool
```

Notes:

- `Type=simple` long-running Uvicorn; `Restart=on-failure` with `RestartSec=5s`
- Does **not** conflict with Prometheus-side collector/publisher units
- Does **not** bind `0.0.0.0`; public HTTPS is provided by nginx (DNS/TLS configured)
- Do **not** place collector SSH material on this host

---

## Trusted collector (consensus)

`scripts/collect_consensus.py` is the first live collector component.

It adapts proven consensus semantics from `scripts/vm_consensus_reference.py` (a
**read-only copy** of the existing monitoring script; do not modify it, and do
not modify the original outside this repo).

### Purpose

On the Prometheus VM, for validator1..validator7:

1. SSH with `~/.ssh/besu_monitor_collector` as `ubuntu`
2. Query `http://127.0.0.1:8545` on each validator for `eth_blockNumber`
3. Query `qbft_getValidatorsByBlockNumber` for the active set (QBFT is not exposed on the public RPC)
4. Compute reference block, drift, sync, quorum
5. Write a **sanitized** snapshot to `output/consensus_snapshot.json`

### Security boundary

- SSH credentials and validator IPs stay on the trusted collector host only.
- The public API host must never receive SSH keys or direct validator access.
- Sanitized output must not contain IPs, aliases, Ethereum addresses, SSH usernames/paths, hostnames, raw JSON-RPC, per-validator rows, or command strings.

### Collector / publisher configuration (required)

Deployment-specific addresses must be supplied outside the repository. There is
**no** baked-in validator inventory or publisher host default.

| Variable / flag | Purpose |
|---|---|
| `BESU_MONITOR_VALIDATORS_FILE` / `--validators-file` | Path to JSON validator inventory (`[{"alias":"...","ip":"..."}, ...]`) |
| `BESU_MONITOR_REMOTE_HOST` / `--remote-host` | Publisher destination host (API/backend SSH target) |

CLI flags take precedence over the corresponding environment variables. Missing
or empty values fail closed (non-zero exit) before SSH/SCP. Example inventory
shape (documentation addresses only): `deploy/examples/validators.example.json`.

The reference deployment loads these via systemd
`EnvironmentFile=-/etc/besu-monitor/collector.env` on the consensus and
infrastructure service units (host-local file; not committed).

### How to run (on the Prometheus collector VM)

Collector only:

```bash
python3 scripts/collect_consensus.py \
  --output output/consensus_snapshot.json \
  --validators-file /path/to/validators.json \
  --max-drift 2 \
  --ssh-identity ~/.ssh/besu_monitor_collector \
  --workers 7
```

**Proven manually on Prometheus:** collector → sanitized `consensus_snapshot.json`.

### Consensus pipeline (collect + publish)

`scripts/run_consensus_pipeline.py` orchestrates:

1. Run `collect_consensus.py` (collector identity only; inventory from config)
2. Require successful collection
3. Atomically publish to my-backend with the **publisher** identity:
   - SCP to a temporary filename in `/var/lib/besu-monitor/current`
   - remote `mv` to `consensus_snapshot.json` only after upload succeeds

SSH identities on Prometheus (do **not** combine or reuse):

| Purpose | Identity |
|---|---|
| Validator collection | `~/.ssh/besu_monitor_collector` |
| Publish to my-backend | `~/.ssh/besu_monitor_publisher` |

With host env already set (or equivalent flags):

```bash
python3 scripts/run_consensus_pipeline.py
```

Other defaults: max-drift 2, workers 7, remote path
`ubuntu@<my-backend>:/var/lib/besu-monitor/current/consensus_snapshot.json`
(`<my-backend>` from `BESU_MONITOR_REMOTE_HOST` / `--remote-host`).

**Proven manually end-to-end on Prometheus:**

```text
N validators (from inventory file) -> collect_consensus.py -> output/consensus_snapshot.json
  -> run_consensus_pipeline.py -> temp upload -> atomic rename
  -> my-backend:/var/lib/besu-monitor/current/consensus_snapshot.json
```

Manual command that succeeded on Prometheus (after host config is present):

```bash
cd ~/besu-network-monitor
python3 scripts/run_consensus_pipeline.py
```

### Systemd automation

Version-controlled unit files live under `deploy/systemd/` (install templates).
In the reference V1 deployment the consensus timer is installed and publishes
every 5 minutes. Units load `EnvironmentFile=-/etc/besu-monitor/collector.env`
for deployment-specific settings (no addresses in the unit files themselves).

| File | Role |
|---|---|
| `besu-monitor-consensus.service` | Oneshot: run the proven pipeline once |
| `besu-monitor-consensus.timer` | Trigger that service every 5 minutes |

This timer automates **consensus collection + atomic publication only**.

Infrastructure has a separate service/timer pair (see below). These units do
**not**:

- collect live public chain metrics
- implement Monitor API routes (see `api/` above for health/freshness)
- replace frontend work

#### Install on Prometheus (manual; do not automate from Cursor)

From the project checkout on Prometheus (`~/besu-network-monitor`):

```bash
# 1) Install unit files
sudo cp deploy/systemd/besu-monitor-consensus.service /etc/systemd/system/
sudo cp deploy/systemd/besu-monitor-consensus.timer /etc/systemd/system/
sudo systemctl daemon-reload

# 2) First manual systemd service test (before enabling the timer)
sudo systemctl start besu-monitor-consensus.service
sudo systemctl status besu-monitor-consensus.service --no-pager
sudo journalctl -u besu-monitor-consensus.service -n 100 --no-pager

# 3) Only after a successful oneshot run: enable and start the timer
sudo systemctl enable --now besu-monitor-consensus.timer
systemctl status besu-monitor-consensus.timer --no-pager
systemctl list-timers besu-monitor-consensus.timer --no-pager

# 4) Ongoing log inspection
sudo journalctl -u besu-monitor-consensus.service -f
# or recent history:
sudo journalctl -u besu-monitor-consensus.service -n 200 --no-pager

# 5) Rollback / disable automation (keeps unit files; stops scheduling)
sudo systemctl disable --now besu-monitor-consensus.timer
# optional: also ensure the oneshot is not left in a failed/startable confusion
sudo systemctl stop besu-monitor-consensus.service
```

Notes:

- Service user: `ubuntu`
- Working directory: `/home/ubuntu/besu-network-monitor`
- Exec: `/usr/bin/python3 .../scripts/run_consensus_pipeline.py`
- `EnvironmentFile=-/etc/besu-monitor/collector.env` (host-local; must define `BESU_MONITOR_VALIDATORS_FILE` and `BESU_MONITOR_REMOTE_HOST`)
- SSH identities remain the existing files under `/home/ubuntu/.ssh/` (never embedded in the units)
- Overlap avoidance: `Type=oneshot` + a single named service unit; systemd will not launch a second concurrent copy while one run is still active
- `Persistent=true` on the timer catches up sensibly after reboot/downtime

### Sanitized output (`output/consensus_snapshot.json`)

```json
{
  "schema_version": "1.0",
  "generated_at": "<UTC ISO-8601>",
  "status": "ok|warning|critical",
  "reference_block": 0,
  "validator_count": 0,
  "synced_validators": 0,
  "quorum_required": 0,
  "has_quorum": true,
  "drift": {
    "observed_max_blocks": 0,
    "allowed_max_blocks": 2
  },
  "out_of_sync_count": 0
}
```

Notes:

- `status` is the **source/collection** status from consensus semantics (`ok` / `warning` / `critical`). Monitor health labels (`healthy` / `degraded` / `critical` / `unknown`) are applied only by the API layer.
- `drift.observed_max_blocks` = max observed per-validator drift from the reference head.
- `drift.allowed_max_blocks` = configured sync threshold (`--max-drift`, default 2).
- On collection failure, the process exits non-zero and does **not** overwrite a previous valid snapshot.

### Local collector tests (no live SSH)

```bash
python3 -m unittest tests.test_collect_consensus tests.test_run_consensus_pipeline -v
```

These exercise consensus math/sanitization and pipeline orchestration with fake runners only. They do **not** replace the proven Prometheus / my-backend live tests.

---

## Trusted collector (infrastructure)

`scripts/collect_infrastructure.py` is the read-only **Prometheus-based** infrastructure
collector (V1). Publishing to my-backend is handled by
`scripts/run_infrastructure_pipeline.py` (same atomic publisher as consensus).

### Purpose

On the Prometheus VM, query the local Prometheus HTTP API
(`http://127.0.0.1:9090` by default) and write a **sanitized** availability
snapshot. Prometheus is the authoritative source for this collector.

- Collector: **no SSH** (local Prometheus HTTP only)
- Publisher: dedicated `~/.ssh/besu_monitor_publisher` (same key as consensus)
- **No database**
- Public API exposes infrastructure via `GET /api/v1/infrastructure` (snapshot only)
- Does **not** determine QBFT quorum (that remains the consensus collector)
- Raw Prometheus data is **never** published — only the sanitized snapshot

### 16-system inventory semantics

`systems.monitored` is exactly **16**. Never derive this by counting Prometheus
scrape targets (validators currently have both Besu and node_exporter targets).

| Count | Systems |
|---|---|
| 7 | Validators (each one system) |
| 1 | Primary RPC |
| 1 | Backup RPC |
| 1 | bootnode2 |
| 1 | faucet |
| 1 | Keycloak (`identity_up`) |
| 1 | my-backend (`backend_up`) |
| 1 | Block Explorer |
| 1 | Grafana |
| 1 | Prometheus |
| **16** | **Total** |

### Validators counted once despite dual telemetry

For each validator, infrastructure UP requires **both**:

1. `besu_validators_nginx` target UP
2. `node_exporter` target UP for that same validator

Either signal down ⇒ that validator system is down. Dual scrape targets must not
inflate the system count.

### Health policy (infrastructure only)

| Status | Meaning |
|---|---|
| `healthy` | All 16 systems UP |
| `degraded` | One or more non-critical / redundancy losses (e.g. backup RPC, Grafana, Block Explorer, a single validator infra system) |
| `critical` | Clearly critical dependency unavailable — **primary RPC down** (minimum rule for this phase) |
| `unknown` | Reserved for incomplete/unreliable telemetry; see failure behavior below |

### Failure behavior

Consistent with the consensus collector: if Prometheus is unreachable, the API
response is malformed, required series are missing/duplicated, or samples are
invalid, the collector raises an error, exits non-zero, and **does not overwrite**
a previous valid snapshot. Missing telemetry is never treated as UP.

An `unknown` snapshot shape with nullable booleans/counts is defined for schema
documentation (`build_unknown_snapshot`) but is **not** written on failure.

### Sanitized output

Default path: `./infrastructure_snapshot.json` (repo root).

```json
{
  "schema_version": 1,
  "generated_at": "<UTC ISO-8601 Z>",
  "status": "healthy|degraded|critical|unknown",
  "systems": {"monitored": 16, "up": 0, "down": 0},
  "validators": {"monitored": 7, "up": 0, "down": 0},
  "rpc": {"primary_up": true, "backup_up": true},
  "services": {
    "bootnode_up": true,
    "block_explorer_endpoint_up": true,
    "faucet_up": true,
    "identity_up": true,
    "backend_up": true,
    "prometheus_up": true,
    "grafana_endpoint_up": true
  }
}
```

Output must not contain IPs, hostnames, Prometheus labels/jobs, URLs, raw
metric payloads, or validator identities.

### How to run collector only (on the Prometheus VM)

```bash
cd ~/besu-network-monitor
python3 scripts/collect_infrastructure.py \
  --prometheus-url http://127.0.0.1:9090 \
  --output ./infrastructure_snapshot.json
```

Optional environment overrides: `BESU_MONITOR_PROMETHEUS_URL`,
`BESU_MONITOR_INFRA_SNAPSHOT`, `BESU_MONITOR_PROMETHEUS_TIMEOUT`.

**Proven manually on Prometheus:** collector → sanitized `infrastructure_snapshot.json`
(`status=healthy`, `systems=16/16`, `validators=7/7`).

### Infrastructure pipeline (collect + publish)

`scripts/run_infrastructure_pipeline.py` orchestrates the same trusted flow as
consensus:

1. Run `collect_infrastructure.py` against local Prometheus
2. Require a valid sanitized snapshot (complete public schema)
3. Atomically publish to my-backend with the **publisher** identity:
   - SCP to a temporary filename in `/var/lib/besu-monitor/current`
   - remote `mv` to `infrastructure_snapshot.json` only after upload succeeds

```text
Prometheus (local :9090)
  -> collect_infrastructure.py -> output/infrastructure_snapshot.json
  -> run_infrastructure_pipeline.py -> temp upload -> atomic rename
  -> my-backend:/var/lib/besu-monitor/current/infrastructure_snapshot.json
```

- Collection/publishing runs on the **Prometheus VM**
- `my-backend` does **not** SSH into infrastructure or query Prometheus
- Publishing reuses `scripts/snapshot_publisher.py` (shared with consensus)

```bash
cd ~/besu-network-monitor
python3 scripts/run_infrastructure_pipeline.py
```

Defaults align with the consensus pipeline where possible: publisher
`~/.ssh/besu_monitor_publisher`, remote path under
`/var/lib/besu-monitor/current/infrastructure_snapshot.json` on
`<my-backend>` from `BESU_MONITOR_REMOTE_HOST` / `--remote-host`, local
`output/infrastructure_snapshot.json`, Prometheus `http://127.0.0.1:9090`.

### Systemd automation

Unit files under `deploy/systemd/` are install templates. In the reference V1
deployment the infrastructure timer is installed and publishes every 5 minutes.
Units load the same host-local `EnvironmentFile=-/etc/besu-monitor/collector.env`
(at minimum `BESU_MONITOR_REMOTE_HOST`).

| File | Role |
|---|---|
| `besu-monitor-infrastructure.service` | Oneshot: run the infrastructure pipeline once |
| `besu-monitor-infrastructure.timer` | Trigger that service every 5 minutes |

Install steps mirror consensus
(`deploy/systemd/besu-monitor-infrastructure.{service,timer}`).

### Infrastructure collector / pipeline tests (no live Prometheus / SSH)

```bash
python3 -m unittest \
  tests.test_collect_infrastructure \
  tests.test_run_infrastructure_pipeline \
  tests.test_run_consensus_pipeline \
  -v
```

## File-based snapshot builder (earlier PoC)

`scripts/build_snapshot.py` still converts read-only fixtures under `input/` into `output/monitor_snapshot.json` (infrastructure + consensus aggregates). That path validated sanitization before the trusted collector existed.

```bash
python3 scripts/build_snapshot.py
```

Fixtures in `input/` remain private copies of operational reports.

## Out of scope for V1 / not implemented

- Installing/enabling systemd units from this Mac/Cursor session (unit files are in-repo; the reference deployment installs and enables them manually on the target VMs)
- Docker, database, caching/history, alerts, Blockscout, write operations, validator management

## Security note

Always review sanitized JSON before public exposure. Never publish `input/` fixtures or collector SSH material. The public API must never expose IPs, hostnames, validator addresses, SSH details, filesystem paths, raw collector exceptions, raw JSON-RPC, or stack traces.

Deployment-specific validator and publisher network addresses are externalized from the application source and must remain on the trusted collector host only. The reference deployment loads the validator inventory and publisher destination from host-local configuration under `/etc/besu-monitor/`; no production network addresses are required in the current source tree.

**Post-release hardening (after V1.0.0):**

- Production validator and publisher addresses were removed from the current source configuration and moved to host-local deployment configuration. The historical V1.0.0 source may contain deployment-specific network addresses; these are operational topology information, not credentials or authentication secrets. Git history and the V1.0.0 tag were intentionally left unchanged.
- The public Monitor API is protected by nginx per-client rate limiting. The reference deployment uses `10r/s` with `burst=20`; requests exceeding the limit receive HTTP `429 Too Many Requests`. The limit is scoped to the Monitor API virtual host and does not apply to other services on the deployment host.
- The dedicated SSH identities used by the trusted collector/publisher are restricted to the trusted collector source host, with unnecessary SSH capabilities (including forwarding, PTY, agent forwarding, and X11 forwarding) disabled through `authorized_keys` restrictions.

**Residual SSH risk:** the collector identity is not currently constrained by a forced-command allowlist. The source-host restriction and SSH capability restrictions reduce the attack surface, but compromise of both the collector host and its private key could still permit non-interactive commands as the configured remote user. A narrowly scoped forced-command wrapper remains a possible future hardening measure; it is not claimed as part of V1.
