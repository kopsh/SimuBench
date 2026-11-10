// The assistant's settings (#872): protocol, address, model, key (write-only), timeout, context
// length, and a connection test. Values left alone keep the server's defaults.
import { CircleCheck, CircleX } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { Schemas } from "@/api/client";
import { useAssistantConfig, useSaveAssistantConfig, useTestAssistant } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

type Protocol = Schemas["AssistantConfig"]["protocol"];

export function AssistantSettings({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation();
  const config = useAssistantConfig();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("assistant.config.title")}</DialogTitle>
          <DialogDescription>{t("assistant.config.description")}</DialogDescription>
        </DialogHeader>
        {/* Mounted per opening: the form starts from the settings as they are now. */}
        {config.data ? <SettingsForm cfg={config.data} onDone={() => onOpenChange(false)} /> : <Skeleton className="h-64 w-full" />}
      </DialogContent>
    </Dialog>
  );
}

function SettingsForm({ cfg, onDone }: { cfg: Schemas["AssistantConfig"]; onDone: () => void }) {
  const { t } = useTranslation();
  const save = useSaveAssistantConfig();
  const test = useTestAssistant();
  const [protocol, setProtocol] = useState<Protocol>(cfg.protocol);
  const [baseUrl, setBaseUrl] = useState(cfg.base_url ?? "");
  const [model, setModel] = useState(cfg.model ?? "");
  const [apiKey, setApiKey] = useState("");
  const [timeout, setTimeoutS] = useState(String(cfg.timeout_s));
  const [context, setContext] = useState(String(cfg.context_tokens));

  const body = (extra: Partial<Schemas["AssistantConfigUpdate"]> = {}): Schemas["AssistantConfigUpdate"] => ({
    protocol,
    base_url: baseUrl.trim(),
    model: model.trim(),
    timeout_s: Number(timeout) || null,
    context_tokens: Math.round(Number(context)) || null,
    api_key: apiKey ? apiKey : null,
    reset: false,
    ...extra,
  });
  const onSave = (then?: () => void) =>
    save.mutate(body(), {
      onSuccess: () => {
        setApiKey("");
        if (then) then();
        else {
          toast.success(t("assistant.config.saved"));
          onDone();
        }
      },
      onError: (e) => toast.error((e as Error).message),
    });

  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        onSave();
      }}
    >
      <Field id="assistant-protocol" label={t("assistant.config.protocol")}>
        <Select value={protocol} onValueChange={(v) => setProtocol(v as Protocol)}>
          <SelectTrigger id="assistant-protocol" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="openai">{t("assistant.config.openai")}</SelectItem>
            <SelectItem value="anthropic">{t("assistant.config.anthropic")}</SelectItem>
          </SelectContent>
        </Select>
      </Field>
      <Field id="assistant-url" label={t("assistant.config.baseUrl")} hint={t("assistant.config.baseUrlHint")}>
        <Input
          id="assistant-url"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="http://llm.internal:8000/v1"
          className="num"
        />
      </Field>
      <Field id="assistant-model" label={t("assistant.config.model")}>
        <Input id="assistant-model" value={model} onChange={(e) => setModel(e.target.value)} className="num" />
      </Field>
      <Field id="assistant-key" label={t("assistant.config.apiKey")} hint={cfg?.api_key_set ? t("assistant.config.apiKeySet") : undefined}>
        <div className="flex gap-2">
          <Input
            id="assistant-key"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="num"
          />
          {cfg?.api_key_set && (
            <Button type="button" variant="outline" size="sm" className="h-9 shrink-0" onClick={() => save.mutate(body({ api_key: "" }))}>
              {t("assistant.config.apiKeyClear")}
            </Button>
          )}
        </div>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field id="assistant-timeout" label={t("assistant.config.timeout")}>
          <Input id="assistant-timeout" inputMode="decimal" value={timeout} onChange={(e) => setTimeoutS(e.target.value)} className="num" />
        </Field>
        <Field id="assistant-context" label={t("assistant.config.context")} hint={t("assistant.config.contextHint")}>
          <Input id="assistant-context" inputMode="numeric" value={context} onChange={(e) => setContext(e.target.value)} className="num" />
        </Field>
      </div>
      <p className="text-muted-foreground text-[11px]">{t("assistant.config.fromServer")}</p>
      {test.data && (
        <p
          role="status"
          className={`flex items-start gap-1.5 text-[12.5px] ${test.data.ok ? "text-ok" : "text-error"}`}
          data-testid="assistant-test-result"
        >
          {test.data.ok ? <CircleCheck className="mt-0.5 size-3.5 shrink-0" /> : <CircleX className="mt-0.5 size-3.5 shrink-0" />}
          <span className="break-words">{test.data.message}</span>
        </p>
      )}
      <DialogFooter className="gap-2 sm:justify-between">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() =>
            save.mutate(
              body({
                reset: true,
                protocol: null,
                base_url: null,
                model: null,
                timeout_s: null,
                context_tokens: null,
                api_key: null,
              }),
            )
          }
        >
          {t("assistant.config.reset")}
        </Button>
        <div className="flex gap-2">
          <Button type="button" variant="outline" disabled={test.isPending || save.isPending} onClick={() => onSave(() => test.mutate())}>
            {test.isPending ? t("assistant.config.testing") : t("assistant.config.test")}
          </Button>
          <Button type="submit" disabled={save.isPending}>
            {t("assistant.config.save")}
          </Button>
        </div>
      </DialogFooter>
    </form>
  );
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1">
      <Label htmlFor={id} className="text-xs">
        {label}
      </Label>
      {children}
      {hint && <p className="text-muted-foreground text-[11px]">{hint}</p>}
    </div>
  );
}
