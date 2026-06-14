import { redirect } from "next/navigation";
import { getCurrentTenant } from "@/lib/tenant";
import { getOnboarding } from "@/lib/onboarding";
import { OnboardWizard } from "@/components/onboard/OnboardWizard";

export const dynamic = "force-dynamic";

export const metadata = { title: "Welcome to minions — set up" };

export default async function OnboardPage() {
  const tenant = await getCurrentTenant();
  const { step } = await getOnboarding(tenant.tenant_id);
  // Finished tenants (and the founder) never see the wizard.
  if (step === "complete") redirect("/hq");
  return <OnboardWizard initialStep={step} />;
}
