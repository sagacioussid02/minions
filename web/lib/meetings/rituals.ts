/**
 * Static mapping from crew kind → display metadata for the meeting room view.
 *
 * The meeting room renders crew runs as round-tables (multi-agent) or focused
 * solo-work cards. This file is the single source of truth for both how a
 * meeting is *labeled* (what ritual is happening, what's the agenda) and
 * where each role sits at the table.
 *
 * Seat positions are fixed per crew kind so the same ritual always looks the
 * same — investors and operators learn the layout once. Unknown agent_roles
 * within a known crew get the fallback seat slot.
 */

export type SeatPosition =
  | "north"
  | "northeast"
  | "east"
  | "southeast"
  | "south"
  | "southwest"
  | "west"
  | "northwest"
  | "center"; // for the solo focused-work card

export interface MeetingRitual {
  /** Operator-facing label shown in the meeting room header. */
  label: string;
  /** One-sentence agenda template; project name interpolated at render time. */
  agenda: string;
  /** True for round-table crews; false for solo-agent crews like engineer / pr_reviewer. */
  multi_agent: boolean;
  /**
   * agent_role → seat position. Unknown roles fall back to a free slot at
   * render time. Order doesn't matter; positions are absolute.
   */
  seat_layout: Record<string, SeatPosition>;
}

export const MEETING_RITUALS: Record<string, MeetingRitual> = {
  // ---- Multi-agent rituals ----
  planning: {
    label: "Sprint planning",
    agenda:
      "Pitch next sprint's plan items, critique each other's proposals, synthesize a sprint.",
    multi_agent: true,
    seat_layout: {
      product_owner: "north",
      principal_engineer: "northeast",
      manager: "southeast",
      devils_advocate: "south",
      security_champion: "southwest",
    },
  },
  discoverer: {
    label: "Project discovery",
    agenda: "Refresh the project dossier from the codebase and recent merges.",
    multi_agent: true,
    seat_layout: {
      discoverer: "north",
      domain_expert: "east",
      principal_engineer: "south",
      security_champion: "west",
    },
  },
  portfolio_review: {
    label: "Monthly portfolio review",
    agenda: "Re-weight project allocations + budget envelopes for the next month.",
    multi_agent: true,
    seat_layout: {
      ceo: "north",
      cto: "northeast",
      cpo: "east",
      managing_director: "south",
      portfolio_owner: "west",
    },
  },
  monthly_portfolio_review: {
    label: "Monthly portfolio review",
    agenda: "Re-weight project allocations + budget envelopes for the next month.",
    multi_agent: true,
    seat_layout: {
      ceo: "north",
      cto: "northeast",
      cpo: "east",
      managing_director: "south",
      portfolio_owner: "west",
    },
  },
  scrum: {
    label: "Scrum stand-up",
    agenda: "Quick status across in-flight tasks; flag blockers + reassignments.",
    multi_agent: true,
    seat_layout: {
      manager: "north",
      product_owner: "east",
      tech_team_lead: "south",
      engineer: "west",
    },
  },
  code_auditor: {
    label: "Code audit",
    agenda: "Spot-check recent merges for hidden risk or untested paths.",
    multi_agent: true,
    seat_layout: {
      code_auditor: "north",
      security_champion: "east",
      qa_engineer: "south",
      principal_engineer: "west",
    },
  },
  backlog_proposer: {
    label: "Backlog grooming",
    agenda: "Triage open issues + propose new sprint candidates.",
    multi_agent: true,
    seat_layout: {
      product_owner: "north",
      manager: "east",
      principal_engineer: "south",
      domain_expert: "west",
    },
  },

  // ---- Single-agent (focused work) rituals ----
  engineer: {
    label: "Focused engineering",
    agenda: "Implementing an approved decision; opening a draft PR.",
    multi_agent: false,
    seat_layout: { engineer: "center" },
  },
  pr_reviewer: {
    label: "PR review",
    agenda: "Reviewing an open PR; posting a structured verdict comment.",
    multi_agent: false,
    seat_layout: { pr_reviewer: "center" },
  },
  qa: {
    label: "QA review",
    agenda: "Posting the QA review comment on a green PR.",
    multi_agent: false,
    seat_layout: { qa_engineer: "center" },
  },
  security: {
    label: "Security review",
    agenda: "Reviewing the security implications of a proposed change.",
    multi_agent: false,
    seat_layout: { security_champion: "center" },
  },
  security_champion: {
    label: "Security review",
    agenda: "Reviewing the security implications of a proposed change.",
    multi_agent: false,
    seat_layout: { security_champion: "center" },
  },
  refinement: {
    label: "Sprint refinement",
    agenda: "Breaking the sprint plan into refined Tasks with owners.",
    multi_agent: false,
    seat_layout: { manager: "center" },
  },
  devils_advocate: {
    label: "Devil's advocate critique",
    agenda: "Critiquing the latest sprint plan for hidden risk.",
    multi_agent: false,
    seat_layout: { devils_advocate: "center" },
  },
};

const FALLBACK_RITUAL: MeetingRitual = {
  label: "Crew session",
  agenda: "An agent crew is at work.",
  multi_agent: false,
  seat_layout: {},
};

/** Get the ritual metadata for a crew kind. Always returns a valid ritual. */
export function ritualFor(crew: string): MeetingRitual {
  return MEETING_RITUALS[crew] ?? FALLBACK_RITUAL;
}

/**
 * Available fallback positions for agent_roles not explicitly mapped in a
 * ritual's seat_layout. Caller picks the next free one when laying out seats.
 */
export const FALLBACK_SEAT_POSITIONS: SeatPosition[] = [
  "north",
  "northeast",
  "east",
  "southeast",
  "south",
  "southwest",
  "west",
  "northwest",
];

/**
 * Coordinates around an ellipse for each compass seat position, expressed as
 * (x, y) offsets from the center, scaled to the ellipse radii passed in.
 *
 * The eight compass points are evenly spaced around the ellipse. "center"
 * lives at (0, 0) and is used for solo focused-work cards.
 */
export function seatCoords(
  position: SeatPosition,
  rx: number,
  ry: number,
): { x: number; y: number } {
  // 8 compass points clockwise from north. Cardinals sit exactly on the
  // ellipse axes; ordinals sit at 45° offsets (cos/sin of π/4 = ~0.707).
  const D = 0.707; // diagonal scaling factor
  switch (position) {
    case "north":
      return { x: 0, y: -ry };
    case "northeast":
      return { x: rx * D, y: -ry * D };
    case "east":
      return { x: rx, y: 0 };
    case "southeast":
      return { x: rx * D, y: ry * D };
    case "south":
      return { x: 0, y: ry };
    case "southwest":
      return { x: -rx * D, y: ry * D };
    case "west":
      return { x: -rx, y: 0 };
    case "northwest":
      return { x: -rx * D, y: -ry * D };
    case "center":
      return { x: 0, y: 0 };
  }
}
