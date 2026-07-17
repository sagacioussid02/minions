import { redirect } from "next/navigation";
import { getCurrentTenant } from "@/lib/tenant";
import { getOnboarding, saveOnboardingStep } from "@/lib/onboarding";
import {
  installUrl,
  isGithubAppConfigured,
  installationAccountLogin,
  listInstallationRepos,
} from "@/lib/github-app";
import { saveInstallation } from "@/lib/github-installations";
import { listProjects, type PickedRepo } from "@/lib/tenant-projects";
import { StepRepos } from "@/components/onboard/StepRepos";
import { StepManifest } from "@/components/onboard/StepManifest";
import { StepDossier } from "@/components/onboard/StepDossier";

export const dynamic = "force-dynamic";
export const metadata = { title: "Welcome to minions — set up" };

export default async function OnboardPage({
  searchParams,
}: {
  searchParams: Promise<{ installation_id?: string }>;
}) {
  const tenant = await getCurrentTenant();
  const sp = await searchParams;

  // Return from the GitHub App install (Setup URL). Record the installation,
  // stash its id on the wizard payload, then bounce to a clean URL.
  if (sp.installation_id) {
    const installationId = Number(sp.installation_id);
    if (Number.isFinite(installationId)) {
      const login = await installationAccountLogin(installationId).catch(() => "");
      await saveInstallation(tenant.tenant_id, installationId, login);
      await saveOnboardingStep(tenant.tenant_id, "repos", { installation_id: installationId });
    }
    redirect("/onboard");
  }

  const { step, payload } = await getOnboarding(tenant.tenant_id);
  if (step === "complete") redirect("/hq");

  if (step === "manifest") {
    const repos = (payload.repos as PickedRepo[] | undefined) ?? [];
    return <StepManifest repos={repos} />;
  }
  if (step === "dossier") {
    const projects = await listProjects(tenant.tenant_id);
    return <StepDossier projects={projects} />;
  }

  // step === "repos"
  if (!(await isGithubAppConfigured())) {
    return <StepRepos installUrl={null} repos={null} cap={tenant.project_cap} selected={[]} />;
  }
  const installationId = payload.installation_id as number | undefined;
  if (!installationId) {
    return (
      <StepRepos
        installUrl={await installUrl()}
        repos={null}
        cap={tenant.project_cap}
        selected={[]}
      />
    );
  }
  const repos = await listInstallationRepos(installationId).catch(() => []);
  const selected = ((payload.repos as PickedRepo[] | undefined) ?? []).map((r) => r.full_name);
  return (
    <StepRepos
      installUrl={await installUrl()}
      repos={repos}
      cap={tenant.project_cap}
      selected={selected}
    />
  );
}
