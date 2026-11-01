import { CheckCircle2, FileCheck2, Play, XCircle } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { CaseRunResult } from "@/api/client";
import { useDefinitionCases, useRunDefinitionCases } from "@/api/hooks";
import { EmptyState, ErrorState } from "@/components/app/StateViews";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** The workspace's cases files for this definition: run one, see every step's expected and actual
 * reply (T4; instrument-authoring.md 7.2). */
export function CasesTab({ definitionId }: { definitionId: string }) {
  const { t } = useTranslation();
  const files = useDefinitionCases(definitionId);
  const run = useRunDefinitionCases(definitionId);
  const [picked, setPicked] = useState<string | null>(null);
  const [result, setResult] = useState<CaseRunResult | null>(null);
  // The first file until the user picks one; a picked file that disappeared falls back too.
  const selected =
    (picked && files.data?.some((f) => f.path === picked) ? picked : files.data?.[0]?.path) ?? null;

  const start = () => {
    if (!selected) return;
    run.mutate(selected, { onSuccess: (r) => setResult(r) });
  };

  if (files.isLoading) return <Skeleton className="m-4 h-64" />;
  if (files.error) return <ErrorState error={files.error} onRetry={() => void files.refetch()} />;
  if (!files.data?.length) {
    return (
      <EmptyState
        icon={<FileCheck2 className="size-6" />}
        title={t("acceptance.noCaseFiles")}
        body={t("acceptance.noCaseFilesBody")}
      />
    );
  }
  const shown = result && result.path === selected ? result : null;
  return (
    <div className="grid h-full min-h-0 gap-3 p-4 lg:grid-cols-[16rem_1fr]">
      <aside className="bg-card min-h-0 overflow-auto rounded-lg border">
        <header className="border-b px-3 py-2 text-xs font-medium">{t("acceptance.caseFiles")}</header>
        <ul className="divide-y">
          {files.data.map((file) => (
            <li key={file.path}>
              <button
                type="button"
                onClick={() => setPicked(file.path)}
                aria-pressed={file.path === selected}
                className={cn(
                  "hover:bg-accent/50 w-full px-3 py-2 text-left text-[11.5px]",
                  file.path === selected && "bg-accent/60",
                )}
              >
                <span className="num block break-all">{file.path}</span>
                <span className="text-muted-foreground text-[10.5px]">{t("acceptance.caseCount", { count: file.cases })}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="bg-card flex min-h-0 flex-col rounded-lg border">
        <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
          <span className="num text-xs">{selected}</span>
          {shown && (
            <span className="flex items-center gap-1.5 text-[11px]">
              {shown.passed > 0 && (
                <Badge className="bg-ok/10 text-ok font-normal" variant="outline">
                  <CheckCircle2 className="size-3" /> {t("acceptance.passed", { count: shown.passed })}
                </Badge>
              )}
              {shown.failed > 0 && (
                <Badge className="bg-error/10 text-error font-normal" variant="outline">
                  <XCircle className="size-3" /> {t("acceptance.failed", { count: shown.failed })}
                </Badge>
              )}
            </span>
          )}
          <Button size="sm" className="ml-auto h-7 px-2 text-[11px]" onClick={start} disabled={!selected || run.isPending}>
            <Play className="size-3.5" /> {run.isPending ? t("acceptance.running") : t("acceptance.runCases")}
          </Button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto">
          {run.error && <p className="text-error px-3 py-2 text-xs">{(run.error as Error).message}</p>}
          {!shown ? (
            <p className="text-muted-foreground p-3 text-xs">{t("acceptance.caseRunHint")}</p>
          ) : (
            <ul className="divide-y">
              {shown.cases.map((c) => (
                <li key={c.name} className="px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {c.passed ? <CheckCircle2 className="text-ok size-3.5" /> : <XCircle className="text-error size-3.5" />}
                    <span className="text-xs font-medium">{c.name}</span>
                    {c.manual && <span className="text-muted-foreground text-[10.5px]">{c.manual}</span>}
                    <span className="text-muted-foreground num ml-auto text-[10.5px]">{c.seconds.toFixed(2)} s</span>
                  </div>
                  <table className="mt-1 w-full text-[11px]">
                    <thead className="text-muted-foreground">
                      <tr className="text-left">
                        <th className="w-6 py-0.5 font-normal">#</th>
                        <th className="py-0.5 font-normal">{t("acceptance.stepCommand")}</th>
                        <th className="py-0.5 font-normal">{t("acceptance.stepExpected")}</th>
                        <th className="py-0.5 font-normal">{t("acceptance.stepActual")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {c.steps.map((s) => (
                        <tr key={s.index} className={cn(!s.ok && "text-error")}>
                          <td className="num py-0.5 align-top">{s.index}</td>
                          <td className="num py-0.5 align-top break-all">
                            {s.action === "send" ? "› " : "? "}
                            {s.command}
                          </td>
                          <td className="num py-0.5 align-top break-all">{s.expected ?? ""}</td>
                          <td className="num py-0.5 align-top break-all">{s.error ?? s.actual ?? ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!c.passed && c.error && <p className="text-error mt-1 text-[11px]">{c.error}</p>}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
