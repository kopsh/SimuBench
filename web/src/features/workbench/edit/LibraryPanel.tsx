import { CircuitBoard, GripVertical, Plus, Search } from "lucide-react";
import { type DragEvent, type ReactNode, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Catalog } from "@/api/client";
import { useCatalog } from "@/api/hooks";
import { ErrorState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { pickLabel } from "@/i18n";
import { typeAbout, typeSearchText } from "@/lib/component";
import { LIBRARY_MIME, type LibraryItem } from "../canvas/Canvas";
import { addItem } from "./actions";
import { CLASS_IDS, ClassIcon } from "../classes";
import { makerName } from "@/lib/format";
import { libraryGroups } from "./library";


/** Instruments and DUTs that can be placed on the bench (the parts bin of schematic editors). */
export function LibraryPanel() {
  const { t } = useTranslation();
  const catalog = useCatalog();
  const [q, setQ] = useState("");
  const query = q.trim().toLowerCase();

  const items = useMemo(() => {
    const c = catalog.data;
    if (!c) return null;
    const match = (...fields: (string | null | undefined)[]) => !query || fields.some((f) => f?.toLowerCase().includes(query));
    return {
      instruments: libraryGroups(c.instruments.filter((d) => match(d.id, d.model, d.manufacturer, d.instrument_class, d.description)), CLASS_IDS),
      parts: c.components.filter((x) => x.category === "part" && match(...typeSearchText(x))),
    };
  }, [catalog.data, query]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b p-2">
        <div className="relative">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("edit.librarySearch")}
            className="h-7 pl-7 text-xs"
            aria-label={t("edit.librarySearch")}
          />
        </div>
        <p className="text-muted-foreground mt-1.5 px-0.5 text-[11px]">{t("edit.libraryHint")}</p>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        {catalog.isLoading && (
          <div className="space-y-2 p-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-11" />
            ))}
          </div>
        )}
        {catalog.error && <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} />}
        {items && catalog.data && (
          <div className="p-2" aria-label={t("edit.library")}>
            {/* By class, as the home page's library (#1356); the maker shortened, in full in the hint. */}
            {items.instruments.map(({ cls, instruments }) => (
              <Group key={cls} title={t(`acceptance.class.${cls}`, { defaultValue: cls })}>
                {instruments.map((d) => (
                  <Entry
                    key={d.id}
                    catalog={catalog.data}
                    item={{ kind: "instrument", id: d.id }}
                    icon={<ClassIcon cls={d.instrument_class} className="size-4" />}
                    title={d.model}
                    // the user's own copy (#1250): marked, and named by its definition so two copies differ
                    badge={d.workspace ? t("edit.mine") : undefined}
                    subtitle={d.workspace ? `${d.id} · ${makerName(d.manufacturer)}` : `${makerName(d.manufacturer)} · ${d.channels} CH`}
                    hint={[`${d.manufacturer} ${d.model}`, d.description].filter(Boolean).join("\n")}
                  />
                ))}
              </Group>
            ))}
            <Group title={t("workbench.parts")}>
              {items.parts.map((c) => (
                <Entry
                  key={c.type}
                  catalog={catalog.data}
                  item={{ kind: "part", id: c.type }}
                  icon={<CircuitBoard className="size-4" />}
                  title={pickLabel(c.label, c.label_en, c.type)}
                  subtitle={c.type}
                  hint={typeAbout(c) ?? c.doc ?? undefined}
                />
              ))}
            </Group>
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-3">
      <h3 className="text-muted-foreground mb-1 px-1 text-[11px] font-medium tracking-wide uppercase">{title}</h3>
      <ul className="space-y-1">{children}</ul>
    </section>
  );
}

function Entry({
  catalog,
  item,
  icon,
  title,
  subtitle,
  hint,
  badge,
}: {
  catalog: Catalog;
  item: LibraryItem;
  icon: ReactNode;
  title: string;
  subtitle: string;
  hint?: string;
  badge?: string;
}) {
  const { t } = useTranslation();
  const onDragStart = (e: DragEvent) => {
    e.dataTransfer.setData(LIBRARY_MIME, JSON.stringify(item));
    e.dataTransfer.effectAllowed = "copy";
  };
  return (
    <li
      draggable
      onDragStart={onDragStart}
      title={hint}
      data-testid={`library-${item.id}`}
      className="group bg-card hover:border-primary/50 flex cursor-grab items-center gap-2 rounded-md border px-2 py-1.5 active:cursor-grabbing"
    >
      <GripVertical className="text-muted-foreground/50 size-3.5 shrink-0" />
      <span className="text-muted-foreground shrink-0">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-[12.5px] leading-tight font-medium">{title}</span>
          {badge && (
            <span className="bg-primary/10 text-primary shrink-0 rounded px-1 text-[10px] leading-4 font-medium" data-part="mine">
              {badge}
            </span>
          )}
        </div>
        <div className="text-muted-foreground num truncate text-[11px] leading-tight">{subtitle}</div>
      </div>
      <Button
        variant="ghost"
        size="icon"
        className="size-6 opacity-60 group-hover:opacity-100"
        aria-label={`${t("edit.add")} ${title}`}
        onClick={() => void addItem(catalog, item)}
      >
        <Plus className="size-3.5" />
      </Button>
    </li>
  );
}
