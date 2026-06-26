const TOTAL = 3;

/** Shared card + progress chrome for the onboarding steps. */
export function OnboardShell({
  stepIndex,
  title,
  children,
}: {
  stepIndex: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-canvas)] p-6">
      <div className="w-full max-w-xl rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-8">
        <div className="mb-6 flex items-center gap-2">
          {Array.from({ length: TOTAL }).map((_, i) => (
            <div
              key={i}
              className={`h-1.5 flex-1 rounded-full ${
                i <= stepIndex ? "bg-[var(--accent)]" : "bg-[var(--line)]"
              }`}
            />
          ))}
        </div>
        <div className="mb-1 font-mono text-xs uppercase tracking-wider text-[var(--text-muted)]">
          Step {stepIndex + 1} of {TOTAL}
        </div>
        <h1 className="mb-4 text-2xl font-semibold tracking-tight text-[var(--text-primary)]">
          {title}
        </h1>
        {children}
      </div>
    </div>
  );
}
