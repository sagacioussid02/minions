import { redirect } from "next/navigation";
import { getCurrentTenant } from "@/lib/tenant";
import { isOnboardingComplete } from "@/lib/onboarding";

/**
 * Onboarding gate for the whole console (P6). Any /hq/* page is only reachable
 * once the tenant has finished onboarding; otherwise we send them to the
 * wizard. The founder is seeded `complete`, so this is a no-op for them.
 *
 * Intentionally renders no chrome — each page still owns its own header /
 * sidebar (see P1). This layout exists purely for the gate.
 */
export default async function HqLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const tenant = await getCurrentTenant();
  if (!(await isOnboardingComplete(tenant.tenant_id))) {
    redirect("/onboard");
  }
  return <>{children}</>;
}
