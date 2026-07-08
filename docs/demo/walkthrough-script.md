# Minions — demo walkthrough & screen-recording script

A tight, repeatable click-path for a 2–3 minute screen recording (LinkedIn /
Medium / YC). Optimised for **outcome over theater**: lead with a real
approval → PR, keep the eye-candy (3D room) as a 10-second closer.

> Record at 1920×1080, browser zoom 100%, hide bookmarks bar. Use a clean
> operator account. Narration lines are in **quotes**; keep them short.

---

## 0. Prep (once, before recording)

```bash
# 1. Apply migrations (creates site_health_samples + renewal_reminders)
minions db migrate

# 2. Seed a believable Sentry page: 24h of uptime/latency history, TLS cert
#    expiry states, and a spread of renewals (ok → amber → red → overdue).
#    Requires MINIONS_DATABASE_URL; writes under MINIONS_FOUNDER_TENANT_ID.
python scripts/seed_sentry_demo.py

# 3. (Optional) a zero-spend planning run to populate a fresh Decision:
minions plan demo            # dry-run, $0

# 4. Start the console
cd web && npm run dev        # http://localhost:3000/hq
```

To reset between takes: `python scripts/seed_sentry_demo.py` (idempotent) or
`python scripts/seed_sentry_demo.py --clear-only`.

---

## 1. The hook — the org, not a chatbot (0:00–0:20)

- **Scene:** `/hq` (Live) — the hero strip + Floor of named agents.
- **Do:** slow-scroll the roster of agents once.
- **Say:** *"This isn't one coding assistant. It's a whole AI engineering
  org — a Product Owner, a Principal Engineer, auditors, a security champion —
  each a named agent with its own role and model tier. I'm the only human."*

## 2. The spine — human-in-the-loop (0:20–0:55)

- **Scene:** Sprint board → a Decision in **Awaiting you**.
- **Do:** open the Decision Record; show the plan + the Devil's Advocate
  critique; click **Approve**.
- **Say:** *"Every meaningful action stops at me. The crew proposes a sprint,
  argues it out, and writes a Decision Record — but nothing proceeds until I
  approve. On approval it opens a **draft** PR. It never merges to main."*

## 3. The payoff — a real PR (0:55–1:25)

- **Scene:** the draft PR on GitHub opened by the engineer crew, with the
  Code Auditor + Security Champion verdicts posted as comments.
- **Say:** *"The engineer crew implements it and opens a draft PR. The auditor
  and security champion review it automatically and post structured verdicts.
  I get the final call."*

## 4. The differentiator — Sentry / operations radar (1:25–2:15)

- **Scene:** `/hq/sentry`.
- **Do:** point at, in order:
  1. **Renewal radar** at the top — *"Cinder's TLS domain renewal is 2 days
     overdue, and an API key is due for rotation in 3 days. Dates only — the
     agents never read the secret itself."*
  2. **Per-project health** — Beacon's `/api/health` shows a real incident dip
     in its 24h uptime; hover the p99 spike.
  3. **Cert badges** — Cinder's `🔒 TLS in 4d` badge glowing red.
- **Say:** *"The org also watches the sites it ships: synthetic uptime,
  latency percentiles, TLS certificate expiry, and upcoming license and
  credential-rotation deadlines — all surfaced here and rolled into the Friday
  digest. It flags the boring operational stuff that takes teams down."*

## 5. Closer — the 3D room (2:15–2:30)

- **Scene:** `/hq/meetings/<run_id>/3d` — the round-table.
- **Do:** let it orbit once.
- **Say:** *"And yes — you can literally watch them meet. But the point isn't
  the room. It's that a human stays in control of an org that runs itself."*

---

## Alternate 60-second cut (LinkedIn autoplay)

1. Live floor (5s) → 2. Approve a Decision (15s) → 3. Draft PR + review
verdicts (20s) → 4. Sentry renewal radar + red cert badge (15s) → 5. 3D room
(5s). Same narration, trimmed.

## Do / Don't

- **Do** show a real repo, real PR URL, real approval.
- **Do** keep Sentry in — it's concrete, differentiated, and non-obvious.
- **Don't** open the pitch with the 3D room. It reads as "optimised for cool."
- **Don't** narrate the architecture. Narrate the *outcome*.
