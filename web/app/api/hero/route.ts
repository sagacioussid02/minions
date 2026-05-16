import { NextResponse } from "next/server";
import { getHeroEvent } from "@/lib/queries";
import { HeroEventSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const hero = await getHeroEvent();
    return NextResponse.json(HeroEventSchema.parse(hero));
  } catch (err) {
    console.error("[/api/hero]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
