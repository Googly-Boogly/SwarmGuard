# SwarmGuard Research Console

React/TypeScript local frontend for the Stage 4.5 FastAPI control service. Full instructions: [User guide](../docs/USER_GUIDE.md); architecture and trust boundaries: [Frontend architecture](../docs/FRONTEND_ARCHITECTURE.md).

From the repository root, activate `.venv`, provision the local operator using `python setup_console_operator.py`, and start `python -m uvicorn console_main:app --host 127.0.0.1 --port 8000 --ws-max-size 16384 --limit-concurrency 32` (one worker). Then:

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. `/api`, `/observe`, and `/ws` are proxied to loopback FastAPI. There is no browser API key configuration.

```bash
npm run lint       # TypeScript static checks
npm test           # Vitest + React Testing Library
npm run build      # production bundle
npx playwright install chromium
npm run test:e2e   # isolated backend/database and real browser flows; ports 8000/5173 must be free
SWARMGUARD_PREVIEW=1 npm run test:e2e  # test built production assets (run build first)
```

The application uses Tailwind v4's Vite integration and locally owned shadcn-style Radix UI components, so no separate Tailwind config file is required. Generated response types are in `src/lib/api/schema.d.ts`; regenerate with the commands in the architecture document. Important responses also receive runtime Zod validation.

Mock results are synthetic infrastructure validation. Browser live execution is unavailable; requests for it are rejected by the backend. Workflow controls and human approval decisions require a server-side operator session and CSRF token. No dynamic agents, arbitrary commands, SQL, or browser policy editing are exposed.
