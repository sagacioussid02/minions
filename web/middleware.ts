import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

// Public routes: the marketing landing, Clerk's own auth pages, and the
// inbound webhook endpoints. The webhook routes don't exist yet (they
// arrive in later phases) but are listed now so they're never gated once
// added. Everything else requires a Clerk session.
const isPublicRoute = createRouteMatcher([
  "/",
  "/sign-in(.*)",
  "/sign-up(.*)",
  "/api/clerk-webhook",
  "/api/github-webhook",
  "/api/billing-webhook",
]);

const isApiRoute = createRouteMatcher(["/api(.*)"]);

export default clerkMiddleware(async (auth, req) => {
  if (isPublicRoute(req)) return;

  const { userId, redirectToSignIn } = await auth();
  if (!userId) {
    // API callers get a clean 401; browsers get a redirect to sign-in.
    if (isApiRoute(req)) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }
    return redirectToSignIn();
  }
});

export const config = {
  matcher: [
    // Run on every route except Next internals and static asset files...
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|css|js)$).*)",
    // ...and always on API routes.
    "/(api|trpc)(.*)",
  ],
};
