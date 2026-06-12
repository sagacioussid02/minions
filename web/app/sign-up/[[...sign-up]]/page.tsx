import { SignUp } from "@clerk/nextjs";

// fallbackRedirectUrl lands new users in HQ for now. P6 repoints this to
// /onboard once the onboarding wizard exists.
export default function SignUpPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-canvas)] p-6">
      <SignUp fallbackRedirectUrl="/hq" />
    </div>
  );
}
