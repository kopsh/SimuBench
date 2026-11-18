import { BookOpen, FlaskConical, ListTree } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { Verdict } from "./model";

/**
 * The acceptance page's answer first (#1352): how much of the manual's command set runs, which
 * cases stand behind it (and how the last run went), and the document it follows. Each part opens
 * the section that has the detail.
 */
export function VerdictBar({
  verdict,
  document,
  onSection,
}: {
  verdict: Verdict;
  document: string | null | undefined;
  onSection: (section: "commands" | "cases" | "reference") => void;
}) {
  const { t } = useTranslation();
  const { coverage, cases, run } = verdict;
  return (
    <div className="bg-card col-span-full flex flex-wrap items-stretch gap-2 rounded-lg border p-2" data-testid="acceptance-verdict">
      <Part icon={<ListTree className="size-4" />} label={t("acceptance.verdict.commands")} onClick={() => onSection("commands")} testId="verdict-commands">
        {coverage ? (
          <>
            <span className="num text-base font-semibold">{coverage.percent}%</span>
            <span className="text-muted-foreground num text-[11px]">
              {t("acceptance.verdict.coverage", { implemented: coverage.implemented, accepted: coverage.accepted, total: coverage.total })}
            </span>
            {coverage.fromModel && (
              <span className="text-muted-foreground text-[11px]" data-testid="verdict-coverage-inherited">
                {t("acceptance.verdict.inheritedCoverage", { model: coverage.fromModel, count: coverage.changed })}
              </span>
            )}
          </>
        ) : (
          <span className="text-muted-foreground text-[11px]">{t("acceptance.verdict.noCoverage")}</span>
        )}
      </Part>
      <Part icon={<FlaskConical className="size-4" />} label={t("acceptance.verdict.cases")} onClick={() => onSection("cases")} testId="verdict-cases">
        {cases.builtinFiles + cases.workspaceFiles === 0 ? (
          <span className="text-muted-foreground text-[11px]">{t("acceptance.verdict.noCases")}</span>
        ) : (
          <>
            <span className="num text-base font-semibold">{t("acceptance.caseCount", { count: cases.cases })}</span>
            <span className="text-muted-foreground text-[11px]">
              {t("acceptance.verdict.files", { builtin: cases.builtinFiles, workspace: cases.workspaceFiles })}
            </span>
          </>
        )}
        {run && (
          <span className={cn("text-[11px]", run.failed ? "text-error" : "text-ok")}>
            {t("acceptance.verdict.lastRun", { passed: run.passed, failed: run.failed })}
          </span>
        )}
      </Part>
      {document && (
        <Part icon={<BookOpen className="size-4" />} label={t("acceptance.verdict.reference")} onClick={() => onSection("reference")} testId="verdict-reference">
          <span className="num text-[13px] font-medium">{document}</span>
        </Part>
      )}
    </div>
  );
}

function Part({ icon, label, onClick, children, testId }: { icon: ReactNode; label: string; onClick: () => void; children: ReactNode; testId: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      className="hover:bg-accent/50 focus-visible:ring-ring flex min-w-48 flex-1 items-start gap-2.5 rounded-md px-3 py-2 text-left outline-none focus-visible:ring-2"
    >
      <span className="text-muted-foreground mt-0.5">{icon}</span>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-muted-foreground text-[11px]">{label}</span>
        {children}
      </span>
    </button>
  );
}
