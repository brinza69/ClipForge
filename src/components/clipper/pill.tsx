// The one-line toggle every choice group in the clipper forms is built from.
// Lifted out of source-form.tsx unchanged when that file crossed the repo's
// 500-line limit; two files render these now.

export function Pill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
        active
          ? "border-primary/60 bg-primary/10 text-primary"
          : "border-border/50 text-muted-foreground hover:bg-accent hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}
