import { Plus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api, unwrap } from "@/api/client";
import { useStartSession } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useWorkbench } from "../workbench/store";

const slug = (name: string) =>
  name
    .trim()
    .replace(/[^\p{L}\p{N}_-]+/gu, "_")
    .replace(/^_+|_+$/g, "") || "bench";

/** Create an empty bench file, start it and open the workbench in Edit mode. */
export function NewBenchDialog() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const start = useStartSession();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [path, setPath] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const filePath = path ?? `${slug(name)}.bench.yaml`;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      await unwrap(
        api.PUT("/api/benches/file", {
          body: { path: filePath, base_sha256: null, data: { schema: "simubench.bench/1", name: name.trim() } },
        }),
      );
      const s = await start.mutateAsync(filePath);
      toast.success(t("edit.created", { path: filePath }));
      useWorkbench.getState().setMode("edit");
      navigate(`/s/${s.id}`);
    } catch (err) {
      toast.error(t("errors.generic"), { description: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="h-8">
          <Plus className="size-3.5" /> {t("edit.newBench")}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={(e) => void submit(e)} className="space-y-4">
          <DialogHeader>
            <DialogTitle>{t("edit.newBench")}</DialogTitle>
            <DialogDescription>{t("edit.libraryHint")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="bench-name">{t("edit.benchName")}</Label>
            <Input id="bench-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="awg_rc_scope" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="bench-path">{t("edit.newBenchPath")}</Label>
            <Input id="bench-path" className="num" value={filePath} onChange={(e) => setPath(e.target.value)} />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={!name.trim() || busy}>
              {t("edit.newBenchCreate")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
