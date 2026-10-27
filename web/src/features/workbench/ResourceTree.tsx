import { Activity, AudioWaveform, Cable, CircuitBoard, Cpu, Network } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { SessionInfo } from "@/api/client";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { instrumentTitle } from "./model";
import { type Selection, useWorkbench } from "./store";

const ICONS: Record<string, typeof Cpu> = { oscilloscope: Activity, function_generator: AudioWaveform };

export function ResourceTree({ session }: { session: SessionInfo }) {
  const { t } = useTranslation();
  const { selection, select, openScreen } = useWorkbench();
  const is = (s: Selection) => JSON.stringify(s) === JSON.stringify(selection);
  return (
    <ScrollArea className="h-full">
      <nav className="p-2 text-[12.5px]" aria-label={t("workbench.resources")}>
        <Group title={t("workbench.instruments")}>
          {session.instruments.map((i) => {
            const Icon = ICONS[i.instrument_class] ?? Cpu;
            const s = { kind: "instrument", id: i.name } as const;
            return (
              <Item key={i.name} active={is(s)} onClick={() => select(s)} onDoubleClick={() => openScreen(i.name)} icon={<Icon className="size-3.5" />}>
                <span className="truncate">{instrumentTitle(i)}</span>
                {i.errors.length > 0 && <span className="text-error num ml-auto text-[11px]">{i.errors.length}</span>}
              </Item>
            );
          })}
        </Group>
        <Group title={t("workbench.duts")}>
          {session.duts.map((d) => {
            const s = { kind: "dut", id: d.name } as const;
            return (
              <Item key={d.name} active={is(s)} onClick={() => select(s)} icon={<CircuitBoard className="size-3.5" />}>
                <span className="truncate">{d.label || d.name}</span>
              </Item>
            );
          })}
        </Group>
        <Group title={t("workbench.wires")}>
          {session.wires.map((w) => {
            const s = { kind: "wire", id: w.index } as const;
            return (
              <Item key={w.index} active={is(s)} onClick={() => select(s)} icon={<Cable className="size-3.5" />}>
                <span className="num truncate text-[11.5px]">
                  {w.from} → {w.to}
                </span>
              </Item>
            );
          })}
        </Group>
        <Group title={t("workbench.nets")}>
          {session.nets.map((n) => {
            const s = { kind: "net", id: n.id } as const;
            return (
              <Item key={n.id} active={is(s)} onClick={() => select(s)} icon={<Network className="size-3.5" />}>
                <span className="num truncate text-[11.5px]" title={n.members.join(", ")}>
                  {n.id}
                </span>
                <span className="text-muted-foreground num ml-auto text-[11px]">{n.members.length}</span>
              </Item>
            );
          })}
        </Group>
      </nav>
    </ScrollArea>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mb-3">
      <div className="text-muted-foreground mb-1 px-2 text-[11px] font-medium tracking-wide uppercase">{title}</div>
      <ul>{children}</ul>
    </div>
  );
}

function Item({
  children,
  icon,
  active,
  onClick,
  onDoubleClick,
}: {
  children: ReactNode;
  icon: ReactNode;
  active: boolean;
  onClick: () => void;
  onDoubleClick?: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        onDoubleClick={onDoubleClick}
        className={cn(
          "focus-visible:ring-ring flex h-7 w-full items-center gap-2 rounded-md px-2 text-left focus-visible:ring-2 focus-visible:outline-none",
          active ? "bg-primary/12 text-foreground" : "hover:bg-accent text-foreground/90",
        )}
        aria-current={active || undefined}
      >
        <span className="text-muted-foreground shrink-0">{icon}</span>
        {children}
      </button>
    </li>
  );
}
