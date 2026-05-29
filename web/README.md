# minions — operator console

The web dashboard for [minions](../README.md): a live, watchable view of your autonomous AI engineering org. It's a [Next.js 16](https://nextjs.org) app (App Router, React 19, Tailwind) that reads the activity log + crew transcripts the orchestrator writes and renders them as a living workplace.

## What's in here

- **`/meetings`** — the meeting feed. Every crew run shows as a round-table (2D) with agents, speaker highlighting, and a transcript; group rituals are surfaced separately from solo work.
- **`/meetings/[run_id]/3d`** — the same meeting as a **3D round-table** (React Three Fiber), lazy-loaded so Three.js never touches the rest of the bundle.
- **`/` (Live)** + **`/roster`** — your named agents, their status, and what they're working on.
- **`/sprint`** — the kanban/swimlane sprint board built from Decision Records + Tasks.
- **Sidebar** — spend this week, "Right now" counters, and a Questions-for-you inbox.

## Getting started

```bash
pnpm install
cp ../.env.example .env.local     # set DATABASE_URL (Neon Postgres)
pnpm dev                          # → http://localhost:3000
```

The dashboard reads from the **same Postgres the orchestrator writes to**. Point `DATABASE_URL` at a [Neon](https://neon.tech) database (free tier is plenty).

- **Empty DB?** Everything renders with clean empty states — fine for UI work.
- **Want live data?** Run the orchestrator (`minions plan demo --no-dry-run`, the cron jobs, etc.) against the same database and the meetings/roster/board come alive.

## Scripts

```bash
pnpm dev                 # dev server (Turbopack)
pnpm build               # production build
pnpm start               # serve the production build
pnpm lint                # eslint
pnpm exec tsc --noEmit   # typecheck (strict)
```

## Notes for contributors

- Data access lives in `lib/queries.ts` (server-side SQL via `@neondatabase/serverless`) and is validated with Zod schemas in `lib/schemas.ts`.
- Meeting rendering: `components/meetings/` — the 2D `RoundTable`, the `Meeting3D` island, and the shared `useMeetingFeed` hook that drives both 2D and 3D from one feed.
- Keep Three.js / R3F imports inside the dynamically-imported 3D island so they stay out of the shared bundle.

See the repo-root [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the full workflow, and [`ARCHITECTURE.md`](../ARCHITECTURE.md) for how the console fits the larger system.
