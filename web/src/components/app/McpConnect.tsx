import { useQuery } from "@tanstack/react-query";
import { Bot, Check, Copy } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, unwrap } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { mcpSnippets } from "./mcpSnippets";

function CopyBlock({ label, value }: { label: string; value: string }) {
  const { t } = useTranslation();
  const [done, setDone] = useState(false);
  return (
    <div className="space-y-1">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="bg-muted/50 flex items-start gap-1 rounded-md border p-2">
        <pre className="num min-w-0 flex-1 overflow-x-auto text-xs whitespace-pre">{value}</pre>
        <Button
          variant="ghost"
          size="icon"
          className="size-6 shrink-0"
          aria-label={t("common.copy")}
          onClick={() => {
            void navigator.clipboard?.writeText(value).then(() => {
              setDone(true);
              window.setTimeout(() => setDone(false), 1200);
            });
          }}
        >
          {done ? <Check className="text-ok size-3.5" /> : <Copy className="size-3.5" />}
        </Button>
      </div>
    </div>
  );
}

/** "Connect an AI assistant": shown only when the service answers MCP (`serve --mcp`, the Windows
 * SimuBench.exe), so a user who double-clicked finds the address on the page that opened (#1188). */
export function McpConnect() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const health = useQuery({ queryKey: ["health"], queryFn: () => unwrap(api.GET("/api/health")), staleTime: Infinity });
  const url = health.data?.mcp_url;
  if (!url) return null;
  return (
    <>
      <Button variant="ghost" size="sm" className="h-8 gap-1.5" onClick={() => setOpen(true)}>
        <Bot className="size-4" /> {t("mcp.button")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[85vh] overflow-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{t("mcp.title")}</DialogTitle>
            <DialogDescription>{t("mcp.description")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {mcpSnippets(url).map((s) => (
              <CopyBlock key={s.id} label={t(`mcp.${s.id}`)} value={s.value} />
            ))}
            <p className="text-muted-foreground text-xs">{t("mcp.note")}</p>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
