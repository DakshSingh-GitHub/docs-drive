# DOCSDRIVE_FRONTEND_ARCHITECTURE.md

Frontend integration architecture for the DocsDrive 3D workstation
experience. Backend is **frozen** — this document describes frontend-only
work that consumes the existing FastAPI contracts.

```
Browser
└── Next.js (React 19, app router, port 3000)
    ├── /                → landing page (3D workstation embedded, staged rollout)
    ├── /workstation     → full-screen iframe → /docsdrive-world/
    ├── /terminal        → DocsDrive terminal (standalone page)
    │   └── /terminal?embed=1 → chrome-free terminal for CRT iframe
    └── existing routes  → untouched

    public/docsdrive-world/   ← built by npm run docsdrive:world:build
    │   (isolated webpack build of the vendored 3D environment)
    │
    └── CSS3D iframe → /terminal?embed=1   (same origin)
            postMessage: mousemove / keydown / keyup → outer camera
```

## Layers

### 1. `vendor/henry-portfolio-outer/` — vendored reference (MIT)

Read-only reference implementation. Attribution in
`THIRD_PARTY_NOTICES.md`. Our integration-specific edits live in
`vendor/docsdrive-world/`, a derived copy — never edit the upstream clone.

### 2. `scripts/build-docsdrive-world.mjs` + `npm run docsdrive:world:build`

Builds the 3D environment with the original webpack pipeline (Three 0.137.5,
React 17, GSAP — preserved), with output redirected to
`public/docsdrive-world/`. The command is reproducible and the output is
committed so the site works without rebuilding the vendor app.

### 3. `/workstation` route

Full-viewport iframe of `/docsdrive-world/index.html` plus:
- a loading overlay while the world boots,
- graceful fallback content when WebGL is unavailable,
- reduced-motion consideration.

### 4. `/terminal` route (DocsDrive terminal)

A self-contained terminal application rendered inside the CRT:
- BIOS boot sequence ("DOCSDRIVE SYSTEMS / DOCSDRIVE BIOS / …") with typed
  lines and skip via click/key, then a real command shell.
- Commands: `help`, `clear`, `about`, `health`, `analyze-pr <url> <n>`,
  `runs [limit]`, `finding <run_id> <finding_id>`, `scenarios`,
  `demo <scenario_id>`, `fix <finding_id>`.
- Backend calls use only existing endpoints (`/api/analyze-pr`,
  `/api/runs`, `/api/runs/{id}/findings/{fid}`, `/api/generate-fix`,
  `/api/fix/apply`, `/api/analyze-diff`, `/api/playground/scenarios`,
  `/api/health`) with `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).
- In embed mode the page posts `mousemove`, `keydown`, `keyup` messages to
  `window.parent` so the 3D camera reacts to interaction inside the screen —
  the exact protocol the outer world expects (see scene audit §5).
- No fake results: if the backend is unreachable, commands report that
  clearly instead of simulating output.

### 5. Backend protection contract

- Protected paths: `backend/**`, `action.yml`, `Dockerfile`,
  `docker-compose.yml`, `start.sh`, `vercel.json`, `MIGRATION.md`.
- Verified by `git diff` before/after and by re-running the backend test
  suite (baseline: **54 passed**; 4 API test modules cannot collect because
  `fastapi` is absent from `requirements.txt` — pre-existing, unchanged).

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Base URL of the drift API used by the terminal |
