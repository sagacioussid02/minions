/**
 * Zero-signup demo mode for the operator console.
 *
 * A stranger who clones this repo has no Neon database, no Clerk app and
 * no GitHub App — and until now every console surface either demanded a
 * sign-in or threw `MINIONS_DATABASE_URL is not set`. Demo mode gives
 * them one public, read-only page (`/demo`) rendered entirely from the
 * sample data below: no account, no signup, no database.
 *
 * When is it on?
 * - `MINIONS_DEMO_MODE=1` forces it on (even with a database configured,
 *   e.g. for a public demo deployment).
 * - `MINIONS_DEMO_MODE=0` forces it off.
 * - Unset: it turns on only when NO database URL is configured — i.e.
 *   exactly the fresh-clone situation. A real deployment with a database
 *   URL and no flag is completely unchanged.
 *
 * Hard limits, by construction:
 * - This module is pure data + a pure gate. It imports nothing from
 *   `lib/db`, runs no query, and is only read by `/demo`, the landing
 *   page's demo CTA, and the middleware's public-route list.
 * - The demo page has no forms, no server actions and no API calls, so
 *   there is nothing a visitor can mutate and no way to reach the
 *   approval-gated agent flow. The approval gate is shown as a labelled
 *   simulation with disabled buttons.
 * - Signed-in behaviour (`/hq/*`, tenant scoping, billing, approvals) is
 *   untouched: when a database IS configured, nothing about those paths
 *   changes.
 */

export function demoModeEnabled(
  env: Record<string, string | undefined> = process.env,
): boolean {
  const flag = env.MINIONS_DEMO_MODE?.trim().toLowerCase();
  if (flag === "1" || flag === "true" || flag === "yes") return true;
  if (flag === "0" || flag === "false" || flag === "no") return false;
  // Fresh clone: no database configured, so the real console cannot run.
  // The demo page is sample data only, so enabling it here is safe.
  return !env.MINIONS_DATABASE_URL && !env.DATABASE_URL;
}

export interface DemoAgent {
  role: string;
  status: "working" | "reviewing" | "waiting" | "idle";
  workingOn: string;
}

export interface DemoSprintItem {
  title: string;
  ownerRole: string;
  detail: string;
}

export interface DemoSprintColumn {
  stage: string;
  items: DemoSprintItem[];
}

export const DEMO_ORG = {
  name: "Acme Widgets",
  project: "acme-storefront",
  plan: "demo",
  weeklySpendUsd: 12.4,
  weeklyCapUsd: 50,
  prsShippedThisWeek: 6,
  openQuestions: 2,
} as const;

/** Sample crew — roles mirror the real taxonomy in `lib/roles.ts`. */
export const DEMO_ROSTER: DemoAgent[] = [
  {
    role: "product_owner",
    status: "waiting",
    workingOn: "Sprint 14 plan is drafted and waiting at the approval gate below.",
  },
  {
    role: "tech_team_lead",
    status: "working",
    workingOn: "Breaking the checkout revamp into engineer-sized tasks.",
  },
  {
    role: "senior_engineer",
    status: "working",
    workingOn: "PR #132 — guest checkout: address form validation.",
  },
  {
    role: "engineer",
    status: "working",
    workingOn: "PR #131 — cart totals include tax estimates.",
  },
  {
    role: "qa_engineer",
    status: "reviewing",
    workingOn: "Reviewing PR #130 — regression pass on the payment flow.",
  },
  {
    role: "code_auditor",
    status: "reviewing",
    workingOn: "Auditing PR #132 for input-handling issues.",
  },
  {
    role: "devils_advocate",
    status: "idle",
    workingOn: "Challenged the sprint 14 plan: flagged the address-API dependency.",
  },
  {
    role: "cloud_devops",
    status: "idle",
    workingOn: "Preview deploys for open PRs are green.",
  },
];

export const DEMO_SPRINT: DemoSprintColumn[] = [
  {
    stage: "Proposed",
    items: [
      {
        title: "Saved payment methods",
        ownerRole: "product_owner",
        detail: "In the sprint 14 plan — starts only if the plan is approved.",
      },
      {
        title: "Address autocomplete",
        ownerRole: "tech_team_lead",
        detail: "Blocked on the address-API key the Devil's Advocate flagged.",
      },
    ],
  },
  {
    stage: "Building",
    items: [
      {
        title: "Guest checkout — address form",
        ownerRole: "senior_engineer",
        detail: "PR #132, checks passing, auditor review pending.",
      },
      {
        title: "Cart tax estimates",
        ownerRole: "engineer",
        detail: "PR #131, in progress.",
      },
    ],
  },
  {
    stage: "In review",
    items: [
      {
        title: "Payment flow regression fix",
        ownerRole: "qa_engineer",
        detail: "PR #130 — QA reviewing; a human still merges.",
      },
    ],
  },
  {
    stage: "Shipped",
    items: [
      {
        title: "One-page checkout shell",
        ownerRole: "senior_engineer",
        detail: "PR #127 merged this week after approval.",
      },
      {
        title: "Order confirmation email",
        ownerRole: "engineer",
        detail: "PR #125 merged this week after approval.",
      },
    ],
  },
];

/**
 * The approval gate, shown as a simulation. In a real deployment this is
 * the one human decision the whole org waits on; here the buttons are
 * disabled and clicking is impossible by design.
 */
export const DEMO_APPROVAL = {
  title: "Sprint 14 plan — checkout revamp",
  proposedBy: "product_owner",
  summary: [
    "Add saved payment methods for returning customers.",
    "Add address autocomplete once the API key is provisioned.",
    "Two engineers, one QA pass, estimated $18 of the $50 weekly cap.",
  ],
  challenge:
    "Devil's Advocate: the address-API dependency is unproven — approve without it, or wait?",
} as const;

export const DEMO_ACTIVITY: Array<{ when: string; text: string }> = [
  { when: "2m ago", text: "Product Owner submitted the sprint 14 plan for approval." },
  { when: "18m ago", text: "QA Engineer started reviewing PR #130 (payment flow fix)." },
  { when: "41m ago", text: "Senior Engineer opened PR #132 (guest checkout form)." },
  { when: "1h ago", text: "Devil's Advocate challenged the sprint 14 plan's API dependency." },
  { when: "3h ago", text: "PR #127 (one-page checkout shell) merged after human approval." },
];
