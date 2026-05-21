import { NextResponse } from "next/server";
import { SPOKESPERSON_ROLES } from "@/lib/spokesperson";
import { SpokespersonRolesSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(
    SpokespersonRolesSchema.parse({ roles: [...SPOKESPERSON_ROLES] }),
  );
}
