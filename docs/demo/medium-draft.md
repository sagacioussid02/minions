# I gave an AI *organization* commit access — and kept my hand on the kill switch

*Draft for Medium / LinkedIn. Replace the `![...]` placeholders with the
screenshots in `docs/screenshots/`. Target length ~1,200 words.*

---

## The pitch in one line

Most "AI coding agents" are a single assistant in your editor. **Minions is a
whole software org** — a Product Owner, a Principal Engineer, a Manager,
engineers, a Code Auditor, a Security Champion, a Devil's Advocate, and an
executive bench — each a named agent with its own role and model tier. They
run real Agile rituals, produce Decision Records, and open draft pull requests.

I'm the only human. I sit at the top and approve.

![The live operator console — a floor of named agents](../screenshots/roster.png)

## Why an org, not an agent

A single agent is great at "write this function." It's bad at everything a
team is *for*: deciding what to build, arguing about trade-offs, catching each
other's mistakes, and owning the boring operational work that actually takes
products down.

So Minions models the team. A planning crew (Product Owner → Principal
Engineer → Manager, with a Devil's Advocate poking holes) debates a sprint and
writes a **Decision Record**. On approval, an engineer crew implements it and
opens a **draft PR**. A Code Auditor and Security Champion review that PR and
post structured verdicts. Every step streams into a live operator console, and
every LLM call is cost-tracked.

![A Decision Record awaiting approval](../screenshots/sprint.png)

## The one constraint that makes it safe

Here's the spine of the whole system: **every meaningful action passes through
a human approval gate.** Nothing merges to `main` without me. The agents
propose; I dispose.

That's not a slogan — it's enforced in four layers:

1. **Prompt** — every agent's system prompt carries the non-negotiable rules.
2. **Tooling** — a filesystem deny-list (`.env*`, `*.pem`, `*.key`,
   `credentials*`); a GitHub client that *has no merge method* and refuses
   `main`/`master`/`develop`; PRs default to draft.
3. **Platform** — GitHub branch protection; the App scope excludes secrets.
4. **Network** — an egress allowlist.

The agents literally *cannot* read your secrets or merge your code. That
constraint is what makes handing them repo access sane.

## The part I didn't expect to matter most: operations

Shipping code is the easy half. What takes small teams down is the unglamorous
operational tail — a site that quietly 503s, a TLS certificate that lapses, a
license that auto-renews-fails, an API key nobody rotated.

So the org watches the things it ships. There's a component I call **Sentry**:

- **Synthetic uptime + latency** — it probes each project's production URL on a
  cadence and tracks status, p50/p99 latency, and 24-hour uptime.
- **TLS certificate expiry** — read straight off the HTTPS handshake (public
  data — no secret access) and flagged as it approaches.
- **A renewal radar** — upcoming license renewals and credential-rotation
  deadlines, declared as *dates* in each project's manifest. Amber at 30 days,
  red at 7 or overdue. It tracks the calendar fact, **never the secret value** —
  staying inside the same "agents never touch secrets" guarantee.

Anything due soon also rolls into the weekly Friday digest, so I get a nudge
before a cert lapses instead of after.

![Sentry — uptime, cert expiry, and the renewal radar](../screenshots/sprint.png)

> This is the tell that an "AI org" is more than a demo: it does the boring,
> non-obvious work a responsible teammate would.

## What running it actually feels like

It runs on a cadence — weekly planning, scrum every other day, daily
monitoring — via scheduled jobs, but always stops at the approval gate. My
inbox fills with proposals and questions from blocked agents. I approve,
reject, or answer. Draft PRs appear. Reviews get posted. A Friday digest tells
me what happened and what's coming due.

It is, genuinely, like being the single human operator of a small company that
never sleeps — with a hard guarantee that it can't do anything irreversible
without me.

## Honest status

It's young and opinionated. It's dogfooded daily on its own portfolio of
projects and works end-to-end: plan → approve → build → PR → review →
monitor. It is not a finished product, and the interesting questions —
how much autonomy is *too* much, where the human gate should sit — are exactly
what I'm working through in the open.

If "an AI org with a human kill switch" is a future you want to poke at, the
code is open. Come break it.

---

*Try it: `git clone`, `minions plan demo` runs the full plan→decision loop in
dry-run mode with **no API keys and $0**. Links in the README.*
