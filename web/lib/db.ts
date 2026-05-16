/**
 * Neon Postgres client used by every read path in the operator console.
 *
 * Reuses the same MINIONS_DATABASE_URL the Python crews write to — there is
 * no separate database. Set MINIONS_DATABASE_URL in `web/.env.local`.
 *
 * The `@neondatabase/serverless` driver works in both the Node and Edge
 * runtimes, so route handlers can run on either.
 */

import { neon, type NeonQueryFunction } from "@neondatabase/serverless";

let _sql: NeonQueryFunction<false, false> | null = null;

export function sql(): NeonQueryFunction<false, false> {
  if (_sql) return _sql;
  const url = process.env.MINIONS_DATABASE_URL;
  if (!url) {
    throw new Error(
      "MINIONS_DATABASE_URL is not set. Add it to web/.env.local (same value the Python crews use).",
    );
  }
  _sql = neon(url);
  return _sql;
}
