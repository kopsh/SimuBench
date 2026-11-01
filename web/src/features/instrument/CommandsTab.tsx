import { ChevronsDownUp, ChevronsUpDown, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { DefinitionCoverage } from "@/api/client";
import type { DefinitionData } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { commandBinding, commandRows, groupCommands, LEVELS, type Level, paramFacts } from "./model";

const LEVEL_CLASS: Record<Level, string> = {
  implemented: "bg-ok/10 text-ok",
  accepted: "bg-warn/10 text-warn",
  not_modeled: "bg-muted text-muted-foreground",
};

/** The command tree: every command of the definition, and the coverage table's other commands when
 * the definition is a real model (T4, instrument-authoring.md 7.2). */
export function CommandsTab({
  commands,
  coverage,
}: {
  commands: NonNullable<DefinitionData["commands"]>;
  coverage: DefinitionCoverage | null | undefined;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [levels, setLevels] = useState<Set<Level>>(() => new Set(LEVELS));
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const rows = useMemo(() => commandRows(commands, coverage), [commands, coverage]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (row.level && !levels.has(row.level)) return false;
      if (!q) return true;
      const haystack = [row.header, row.note, row.section, ...((row.command?.params ?? []).map((p) => p.name))]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [rows, query, levels]);
  const groups = useMemo(() => groupCommands(filtered), [filtered]);
  const allOpen = groups.length > 0 && groups.every((g) => open.has(g.node));
  const toggle = (node: string) =>
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(node)) next.delete(node);
      else next.add(node);
      return next;
    });
  const toggleLevel = (level: Level) =>
    setLevels((s) => {
      const next = new Set(s);
      if (next.has(level)) next.delete(level);
      else next.add(level);
      return next;
    });
  // A search shows what it found; without one the tree starts folded.
  const expanded = (node: string) => query.trim() !== "" || open.has(node);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <div className="relative w-64">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("acceptance.searchCommands")}
            aria-label={t("acceptance.searchCommands")}
            className="h-8 pl-8"
          />
        </div>
        {coverage && (
          <div className="flex items-center gap-1" role="group" aria-label={t("acceptance.levelFilter")}>
            {LEVELS.map((level) => (
              <button
                key={level}
                type="button"
                aria-pressed={levels.has(level)}
                onClick={() => toggleLevel(level)}
                className={cn(
                  "rounded-md border px-2 py-0.5 text-[11px] transition-colors",
                  levels.has(level) ? LEVEL_CLASS[level] : "text-muted-foreground hover:text-foreground border-transparent",
                )}
              >
                {t(`acceptance.level.${level}`)}
                <span className="num ml-1">{coverage.stats[level] ?? 0}</span>
              </button>
            ))}
          </div>
        )}
        <span className="text-muted-foreground ml-auto text-[11px]">{t("acceptance.commandCount", { count: filtered.length })}</span>
        <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px]" onClick={() => setOpen(allOpen ? new Set() : new Set(groups.map((g) => g.node)))}>
          {allOpen ? <ChevronsDownUp className="size-3.5" /> : <ChevronsUpDown className="size-3.5" />}
          {allOpen ? t("internals.collapseAll") : t("internals.expandAll")}
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {groups.length === 0 ? (
          <p className="text-muted-foreground p-4 text-xs">{t("acceptance.noCommands")}</p>
        ) : (
          <ul className="divide-y">
            {groups.map((group) => (
              <li key={group.node}>
                <button
                  type="button"
                  onClick={() => toggle(group.node)}
                  aria-expanded={expanded(group.node)}
                  className="hover:bg-accent/40 flex w-full items-center gap-2 px-3 py-1.5 text-left text-[11px] font-medium"
                >
                  <span className={cn("text-muted-foreground transition-transform", expanded(group.node) && "rotate-90")}>▶</span>
                  <span className="num">{group.node}</span>
                  <span className="text-muted-foreground num ml-auto font-normal">{group.rows.length}</span>
                </button>
                {expanded(group.node) && (
                  <ul className="divide-y border-t">
                    {group.rows.map((row, i) => (
                      <li key={`${row.norm}-${i}`} className="grid grid-cols-[minmax(18rem,1.1fr)_2fr] gap-3 px-3 py-2 text-xs odd:bg-muted/20">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <code className="num text-[11.5px] break-all">{row.header}</code>
                            {row.level && (
                              <span className={cn("rounded px-1.5 py-0.5 text-[10.5px]", LEVEL_CLASS[row.level])}>
                                {t(`acceptance.level.${row.level}`)}
                              </span>
                            )}
                            {!row.level && <span className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10.5px]">{t("acceptance.level.implemented")}</span>}
                          </div>
                          <div className="text-muted-foreground mt-0.5 flex flex-wrap gap-x-2 text-[10.5px]">
                            {row.command && (
                              <>
                                {row.command.set && <span>{t("acceptance.writable")}</span>}
                                {row.command.query && <span>{t("acceptance.queryable")}</span>}
                                {commandBinding(row.command) && (
                                  <span className="num">{t("acceptance.binds", { target: commandBinding(row.command) })}</span>
                                )}
                              </>
                            )}
                            {row.section && <span>{row.section}</span>}
                          </div>
                        </div>
                        <div className="min-w-0">
                          {row.command && (row.command.params ?? []).length > 0 && (
                            <ul className="mb-1 space-y-0.5">
                              {(row.command.params ?? []).map((p) => (
                                <li key={p.name} className="flex flex-wrap items-baseline gap-x-1.5 text-[11px]">
                                  <span className="num">{p.name}</span>
                                  <span className="text-muted-foreground">{paramFacts(p)}</span>
                                </li>
                              ))}
                            </ul>
                          )}
                          {row.command?.reply && (
                            <p className="num text-muted-foreground text-[11px] break-all">
                              {t("acceptance.reply")}: {row.command.reply}
                            </p>
                          )}
                          {row.note && <p className="text-muted-foreground text-[11px]">{row.note}</p>}
                          {row.command?.doc && <p className="text-[11px]">{row.command.doc}</p>}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
