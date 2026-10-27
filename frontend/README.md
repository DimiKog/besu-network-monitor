# Besu Network Monitor — Frontend

Static, read-only dashboard for the Besu Network Monitor API. Built for GitHub
Pages deployment. The FastAPI backend remains the source of truth for health
and metrics.

## Stack

- Next.js (App Router) + React + TypeScript
- Tailwind CSS v4 for layout primitives / reset
- Custom CSS for product styling
- Pure SVG / CSS charts (no chart framework dependency)
- Vitest + Testing Library for focused UI tests
- `output: "export"` static HTML/CSS/JS (no frontend server in production)

## Local development

```bash
cd frontend
npm install
cp .env.example .env.local   # optional overrides
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

`npm run dev` uses `.env.development` so the browser calls `/monitor-api/...`,
which Next.js rewrites to the production API. That avoids needing a production
CORS change for localhost.

To talk to a local API instead:

```bash
export NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8040
npm run dev
```

To call the production API origin directly from the browser (no rewrite), the
API CORS allow-list must include `http://localhost:3000` — see root README
`BESU_MONITOR_CORS_ORIGINS`.

## Production / static export

```bash
cd frontend
npm run build
```

Static files are written to `frontend/out/` and can be published to GitHub Pages
(or any static host).

## API configuration

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `https://api.monitor.dimikog.org` | Monitor API origin |
| `NEXT_PUBLIC_NETWORK_NAME` | `Besu Edu-Net` | Header network label |
| `NEXT_PUBLIC_CHAIN_ID` | `424242` | Header chain ID |
| `NEXT_PUBLIC_BLOCKSCOUT_BASE_URL` | `https://blockexplorer.dimikog.org` | Recent-block links |

## Polling

| Endpoint | Interval | Notes |
|---|---|---|
| `GET /api/v1/overview` | 15s | Above-the-fold health |
| `GET /api/v1/chain` | 15s | Live chain + recent blocks (2s initial offset) |
| `GET /api/v1/consensus` | 60s | Collected telemetry (4s initial offset) |
| `GET /api/v1/infrastructure` | 60s | Collected telemetry (6s initial offset) |

## Checks

```bash
npm run typecheck
npm run lint
npm test
npm run build
```

## Dashboard scope

V1 dashboard sections:

1. Header + overall health + domain summary cards
2. Chain Activity (metrics, interval chart, recent blocks)
3. QBFT Consensus (metrics + recent proposer distribution)
4. Infrastructure (systems, RPC, services)
5. Footer
