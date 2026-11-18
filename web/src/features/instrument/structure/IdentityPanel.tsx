// The build area's identity page (P2-5): maker, model, serial, firmware and the *IDN? format of a
// workspace definition. Each field shows what the instrument runs (a copy's inherited values
// included) and writes only a change, one version per save, through the same checks as any edit.
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { IdentityUpdate } from "@/api/client";
import { useDefinition, useDefinitionDocument, useSaveIdentity } from "@/api/hooks";
import { commit, DraftInput, SKIP } from "@/components/app/DraftInput";

const FIELDS = ["manufacturer", "model", "serial", "firmware", "idn"] as const;
type Key = (typeof FIELDS)[number];

export function IdentityPanel({ definitionId, editable }: { definitionId: string; editable: boolean }) {
  const { t } = useTranslation();
  const doc = useDefinitionDocument(definitionId);
  const definition = useDefinition(definitionId, doc.data?.sha256 ?? "");
  const save = useSaveIdentity(definitionId);
  const d = definition.data;
  const value = (k: Key): string =>
    !d ? "" : k === "manufacturer" || k === "model" ? String(d[k] ?? "") : String(d.identity?.[k] ?? "");
  const write = (k: Key, v: string) => {
    if (!doc.data) return;
    const body: IdentityUpdate = { base_sha256: doc.data.sha256, [k]: v };
    save.mutate(body, {
      onSuccess: () => toast.success(t("acceptance.build.identitySaved")),
      onError: (e) => toast.error((e as Error).message),
    });
  };
  return (
    <div className="space-y-2 px-2 py-2" data-testid="build-identity">
      {FIELDS.map((k) => (
        <label key={k} className="block space-y-0.5">
          <span className="text-muted-foreground text-[10.5px]">{t(`acceptance.build.identity.${k}`)}</span>
          <DraftInput<string>
            text={value(k)}
            disabled={!editable || !d}
            className="num h-7 text-xs"
            aria-label={t(`acceptance.build.identity.${k}`)}
            parse={(typed) => (typed.trim() === "" || typed.trim() === value(k) ? SKIP : commit(typed.trim()))}
            onCommit={(v) => write(k, v)}
          />
        </label>
      ))}
      <p className="text-muted-foreground text-[10.5px]">{t("acceptance.build.identityHint")}</p>
    </div>
  );
}
