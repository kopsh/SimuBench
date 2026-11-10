// "Save as my template" (#875): the instrument, with its settings as defaults, becomes a definition
// of its own; only a name is asked for. The bench's instrument then uses it.
import { BookmarkPlus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useBenchDoc, useSaveTemplate } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

export function SaveTemplateButton({ bench, instrument, label }: { bench: string; instrument: string; label: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <BookmarkPlus className="size-4" /> {t("template.save")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("template.save")}</DialogTitle>
            <DialogDescription>{t("template.hint")}</DialogDescription>
          </DialogHeader>
          {open && <TemplateForm bench={bench} instrument={instrument} initial={label} onDone={() => setOpen(false)} />}
        </DialogContent>
      </Dialog>
    </>
  );
}

function TemplateForm({ bench, instrument, initial, onDone }: { bench: string; instrument: string; initial: string; onDone: () => void }) {
  const { t } = useTranslation();
  const doc = useBenchDoc(bench, false);
  const save = useSaveTemplate();
  const [name, setName] = useState(initial);
  const submit = () => {
    if (!doc.data || !name.trim()) return;
    save.mutate(
      { bench_path: bench, base_sha256: doc.data.sha256, instrument, name: name.trim() },
      {
        onSuccess: (out) => {
          toast.success(t("template.saved", { id: out.definition.id }), {
            description: out.not_folded.length ? t("template.notFolded", { paths: out.not_folded.join("、") }) : t("template.restart"),
          });
          onDone();
        },
        onError: (e) => toast.error((e as Error).message),
      },
    );
  };
  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Input aria-label={t("template.name")} placeholder={t("template.name")} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          {t("common.cancel")}
        </Button>
        <Button type="submit" disabled={!name.trim() || !doc.data || save.isPending}>
          {t("template.save")}
        </Button>
      </DialogFooter>
    </form>
  );
}
