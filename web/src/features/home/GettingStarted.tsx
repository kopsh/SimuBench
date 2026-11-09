import { Bot, Check, Copy, Play, Sparkles, Terminal, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { BenchFile } from "@/api/client";
import { Button } from "@/components/ui/button";
import { load, save } from "@/lib/storage";

/**
 * First-use guide (M5): three ways in — run a bench here, drive it from a script through PyVISA,
 * or let an AI agent build one over MCP. Dismissed once, per browser.
 */
export function GettingStarted({ example, onStart }: { example: BenchFile | undefined; onStart: (b: BenchFile) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(() => !load<boolean>("guide.dismissed", false));
  if (!open) return null;
  const dismiss = () => {
    save("guide.dismissed", true);
    setOpen(false);
  };
  const bench = example?.path ?? "benches/awg_rc_scope.bench.yaml";
  const script = `import pyvisa\nrm = pyvisa.ResourceManager("${bench}@simubench")\nscope = rm.open_resource("scope")\nprint(scope.query("*IDN?"))`;
  return (
    <section
      className="from-primary/[0.07] via-card to-card relative overflow-hidden rounded-xl border bg-gradient-to-br p-5"
      aria-label={t("guide.title")}
    >
      <button
        type="button"
        onClick={dismiss}
        className="text-muted-foreground hover:text-foreground hover:bg-accent absolute top-3 right-3 rounded-md p-1 transition-colors"
        aria-label={t("guide.dismiss")}
      >
        <X className="size-4" />
      </button>
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <Sparkles className="text-primary size-4" />
        {t("guide.title")}
      </h2>
      <p className="text-muted-foreground mt-1 max-w-2xl pr-8 text-xs">{t("guide.subtitle")}</p>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <Step n={1} icon={<Play className="size-4" />} title={t("guide.runTitle")} body={t("guide.runBody")}>
          <Button size="sm" className="w-full" disabled={!example} onClick={() => example && onStart(example)}>
            <Play className="size-3.5" /> {example ? t("guide.runAction", { name: example.name }) : t("guide.noExample")}
          </Button>
        </Step>
        <Step n={2} icon={<Terminal className="size-4" />} title={t("guide.scriptTitle")} body={t("guide.scriptBody")}>
          <Snippet text={script} />
        </Step>
        <Step n={3} icon={<Bot className="size-4" />} title={t("guide.agentTitle")} body={t("guide.agentBody")}>
          <Snippet text="simubench mcp --workspace ." />
        </Step>
      </div>
    </section>
  );
}

function Step({ n, icon, title, body, children }: { n: number; icon: React.ReactNode; title: string; body: string; children: React.ReactNode }) {
  return (
    <div className="bg-card/80 flex min-w-0 flex-col gap-2 rounded-lg border p-3.5">
      <div className="flex items-center gap-2 text-sm font-medium">
        <span className="bg-primary/10 text-primary grid size-7 shrink-0 place-items-center rounded-md">{icon}</span>
        <span className="min-w-0 flex-1">{title}</span>
        <span className="text-muted-foreground num text-[11px]">0{n}</span>
      </div>
      <p className="text-muted-foreground flex-1 text-xs">{body}</p>
      {children}
    </div>
  );
}

function Snippet({ text }: { text: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  return (
    <div className="bg-plot relative rounded-md border">
      <pre className="num overflow-x-auto p-2.5 pr-8 text-[11px] leading-snug text-[var(--plot-text)]">{text}</pre>
      <button
        type="button"
        className="absolute top-1.5 right-1.5 rounded p-1 text-[var(--plot-text)] opacity-70 transition-opacity hover:opacity-100"
        aria-label={t("guide.copy")}
        onClick={() => {
          void navigator.clipboard?.writeText(text).then(() => setCopied(true));
        }}
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </button>
    </div>
  );
}
