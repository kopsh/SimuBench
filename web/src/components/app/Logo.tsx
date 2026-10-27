export function Logo({ className }: { className?: string }) {
  return (
    <span className={className} aria-label="SimuBench">
      <svg viewBox="0 0 32 32" className="inline-block size-5 align-[-4px]" aria-hidden>
        <rect width="32" height="32" rx="7" fill="currentColor" className="text-foreground" />
        <path
          d="M5 19c3 0 3-8 6-8s3 10 6 10 3-12 6-12 3 6 4 6"
          fill="none"
          stroke="var(--ch1)"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <span className="ml-2 font-semibold tracking-tight">SimuBench</span>
    </span>
  );
}
