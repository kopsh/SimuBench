import { GitCompare, History } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { DefinitionChange } from "@/api/client";
import { useDefinitionChanges, useDefinitionHistory } from "@/api/hooks";
import { EmptyState, ErrorState } from "@/components/app/StateViews";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { changeValue, savedAt } from "./model";

export function ChangeLine({ change }: { change: DefinitionChange }) {
  const { t } = useTranslation();
  const detail = change.detail ? t(`acceptance.field.${change.detail}`, { defaultValue: change.detail }) : null;
  // An object added or removed whole (a wire) has no values to show.
  const bare = change.old == null && change.new == null;
  return (
    <li className="flex flex-wrap items-baseline gap-x-1.5 text-[11px]">
      <span className="text-muted-foreground">{t(`acceptance.action.${change.action}`)}</span>
      <span>{t(`acceptance.kind.${change.kind}`, { defaultValue: change.kind })}</span>
      <span className="num break-all">{change.target}</span>
      {detail && <span className="text-muted-foreground">· {detail}</span>}
      {change.action !== "added" && !bare && (
        <span className="num text-muted-foreground break-all">{changeValue(change.old)}</span>
      )}
      {change.action !== "removed" && !bare && (
        <>
          {change.action === "changed" && <span className="text-muted-foreground">→</span>}
          <span className="num break-all">{changeValue(change.new)}</span>
        </>
      )}
    </li>
  );
}

/** A long change list (a first save can add a whole definition) folds to its first lines. */
function ChangeList({ changes }: { changes: DefinitionChange[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const limit = 8;
  const shown = open ? changes : changes.slice(0, limit);
  return (
    <>
      <ul className="space-y-0.5">
        {shown.map((change, i) => (
          <ChangeLine key={`${change.kind}-${change.target}-${change.detail}-${i}`} change={change} />
        ))}
      </ul>
      {changes.length > limit && (
        <button type="button" className="text-muted-foreground hover:text-foreground mt-1 text-[11px] underline-offset-2 hover:underline" onClick={() => setOpen(!open)}>
          {open ? t("acceptance.fewerChanges") : t("acceptance.moreChanges", { count: changes.length - limit })}
        </button>
      )}
    </>
  );
}

/** The definition's versions and what each one changed, in the instrument's language; any two
 * versions can be compared (D35, T3/T4). */
export function HistoryTab({ definitionId, sha, editable }: { definitionId: string; sha: string | undefined; editable: boolean }) {
  const { t } = useTranslation();
  const versions = useDefinitionHistory(definitionId, sha, editable);
  const [pickedBase, setBase] = useState<number | null>(null);
  const [pickedTarget, setTarget] = useState<number | null>(null);
  const list = useMemo(() => versions.data ?? [], [versions.data]);
  // The newest version until the user picks another; a pick that a reload removed falls back.
  const target = pickedTarget !== null && list.some((v) => v.seq === pickedTarget) ? pickedTarget : (list[0]?.seq ?? 0);
  const base = pickedBase !== null && list.some((v) => v.seq === pickedBase) ? pickedBase : (list[1]?.seq ?? 0);
  // With one version there is nothing to compare against yet; the pick itself (0: creation) counts.
  const comparable = target > 0 && (pickedBase !== null || list.length > 1);
  const diff = useDefinitionChanges(definitionId, comparable ? base : 0, comparable ? target : 0);

  if (!editable) {
    return (
      <EmptyState
        icon={<History className="size-6" />}
        title={t("acceptance.noHistory")}
        body={t("acceptance.noHistoryBody")}
      />
    );
  }
  if (versions.isLoading) return <Skeleton className="m-4 h-64" />;
  if (versions.error) return <ErrorState error={versions.error} onRetry={() => void versions.refetch()} />;
  if (list.length === 0) {
    return (
      <EmptyState
        icon={<History className="size-6" />}
        title={t("acceptance.emptyHistory")}
        body={t("acceptance.emptyHistoryBody")}
      />
    );
  }
  return (
    <div className="grid h-full min-h-0 gap-3 p-4 xl:grid-cols-[1fr_1fr]">
      <section className="bg-card flex min-h-0 flex-col rounded-lg border">
        <header className="border-b px-3 py-2 text-xs font-medium">{t("acceptance.versions", { count: list.length })}</header>
        <div className="min-h-0 flex-1 overflow-auto">
          <ul className="divide-y">
            {list.map((v) => (
              <li key={v.seq} className="px-3 py-2">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="num font-medium">v{v.seq}</span>
                  <Badge variant="outline" className="font-normal">
                    {t(`acceptance.source.${v.source}`)}
                  </Badge>
                  <span className="text-muted-foreground num text-[10.5px]">{savedAt(v.saved_at)}</span>
                  <span className="text-muted-foreground num ml-auto text-[10.5px]">{v.sha256.slice(0, 8)}</span>
                </div>
                {v.changes.length === 0 ? (
                  <p className="text-muted-foreground mt-1 text-[11px]">{t("acceptance.noChange")}</p>
                ) : (
                  <div className="mt-1">
                    <ChangeList changes={v.changes} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="bg-card flex min-h-0 flex-col rounded-lg border">
        <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs font-medium">
          <GitCompare className="size-3.5" /> {t("acceptance.compare")}
          <div className="ml-auto flex items-center gap-1.5 text-[11px] font-normal">
            <Select value={String(base)} onValueChange={(v) => setBase(Number(v))}>
              <SelectTrigger className="h-7 w-36" aria-label={t("acceptance.compareBase")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="0">{t("acceptance.created")}</SelectItem>
                {list.map((v) => (
                  <SelectItem key={v.seq} value={String(v.seq)}>
                    v{v.seq}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-muted-foreground">→</span>
            <Select value={String(target)} onValueChange={(v) => setTarget(Number(v))}>
              <SelectTrigger className="h-7 w-36" aria-label={t("acceptance.compareTarget")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {list.map((v) => (
                  <SelectItem key={v.seq} value={String(v.seq)}>
                    v{v.seq}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-auto p-3">
          {!comparable ? (
            <p className="text-muted-foreground text-xs">{t("acceptance.compareOne")}</p>
          ) : diff.isLoading ? (
            <Skeleton className="h-24" />
          ) : diff.error ? (
            <ErrorState error={diff.error} onRetry={() => void diff.refetch()} />
          ) : diff.data && diff.data.changes.length > 0 ? (
            <ChangeList changes={diff.data.changes} />
          ) : (
            <p className="text-muted-foreground text-xs">{t("acceptance.noDiff")}</p>
          )}
        </div>
      </section>
    </div>
  );
}
