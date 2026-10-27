import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleX, Download } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { api, unwrap } from "@/api/client";
import { getToken } from "@/api/token";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

type Check = { name: string; status: string; detail: string };
type Manifest = {
  simubench: string;
  python: string;
  platform: string;
  dependencies: Record<string, string | null>;
  web_ui: { present: boolean; sha256: string | null };
  definitions: { id: string; version?: number; sha256?: string; error?: string }[];
  workspace: string | null;
  record_dir: string | null;
};

/** Download the diagnostics bundle (zip); the access token is sent as a header, never in the file. */
export async function downloadDiagnostics(): Promise<void> {
  const res = await fetch("/api/diagnostics", { headers: { "x-simubench-token": getToken() ?? "" } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const blob = await res.blob();
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "simubench-diagnostics.zip";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

const ICON = { ok: CircleCheck, warn: CircleAlert, fail: CircleX } as const;
const TONE = { ok: "text-ok", warn: "text-warn", fail: "text-error" } as const;

/** About SimuBench: the version manifest and the health checks, and the diagnostics bundle. */
export function AboutDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation();
  const manifest = useQuery({
    queryKey: ["manifest"],
    queryFn: () => unwrap(api.GET("/api/manifest")) as Promise<Manifest>,
    enabled: open,
  });
  const doctor = useQuery({
    queryKey: ["doctor"],
    queryFn: () => unwrap(api.GET("/api/doctor")) as Promise<Check[]>,
    enabled: open,
  });
  const [busy, setBusy] = useState(false);
  const m = manifest.data;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("about.title")}</DialogTitle>
          <DialogDescription>{t("about.description")}</DialogDescription>
        </DialogHeader>
        {m && (
          <dl className="num grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted-foreground">SimuBench</dt>
            <dd data-testid="about-version">{m.simubench}</dd>
            <dt className="text-muted-foreground">Python</dt>
            <dd>{m.python}</dd>
            <dt className="text-muted-foreground">{t("about.platform")}</dt>
            <dd className="break-all">{m.platform}</dd>
            <dt className="text-muted-foreground">{t("about.dependencies")}</dt>
            <dd>
              {Object.entries(m.dependencies)
                .map(([k, v]) => `${k} ${v ?? "—"}`)
                .join(" · ")}
            </dd>
            <dt className="text-muted-foreground">{t("about.definitions")}</dt>
            <dd>{m.definitions.map((d) => `${d.id}${d.version ? ` v${d.version}` : ""}`).join(" · ")}</dd>
            <dt className="text-muted-foreground">{t("about.workspace")}</dt>
            <dd className="break-all">{m.workspace ?? "—"}</dd>
            <dt className="text-muted-foreground">{t("about.records")}</dt>
            <dd className="break-all">{m.record_dir ?? t("about.notRecording")}</dd>
          </dl>
        )}
        <div>
          <h3 className="mb-1.5 text-xs font-medium">{t("about.health")}</h3>
          <ul className="space-y-1" data-testid="about-doctor">
            {(doctor.data ?? []).map((c) => {
              const Icon = ICON[c.status as keyof typeof ICON] ?? CircleAlert;
              return (
                <li key={c.name} className="flex items-start gap-2 text-xs">
                  <Icon className={cn("mt-0.5 size-3.5 shrink-0", TONE[c.status as keyof typeof TONE])} />
                  <span className="w-36 shrink-0">{c.name}</span>
                  <span className="text-muted-foreground num break-all">{c.detail}</span>
                </li>
              );
            })}
          </ul>
        </div>
        <div className="flex items-center gap-3 border-t pt-3">
          <p className="text-muted-foreground flex-1 text-[11px]">{t("about.bundleHint")}</p>
          <Button
            size="sm"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              downloadDiagnostics()
                .catch((e: unknown) => toast.error(t("about.bundleFailed"), { description: String(e) }))
                .finally(() => setBusy(false));
            }}
          >
            <Download className="size-3.5" /> {t("about.bundle")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
