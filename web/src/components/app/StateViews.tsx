import { AlertTriangle, KeyRound, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";

export function EmptyState({ icon, title, body, action }: { icon?: ReactNode; title: string; body?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon && <div className="text-muted-foreground mb-1">{icon}</div>}
      <p className="font-medium">{title}</p>
      {body && <p className="text-muted-foreground max-w-md text-[12.5px]">{body}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Explain what went wrong, where and what to do (glossary.md, copy rules). */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation();
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 401) {
    return <EmptyState icon={<KeyRound className="size-6" />} title={t("connection.unauthorized")} body={t("connection.unauthorizedBody")} />;
  }
  const network = status === 0;
  return (
    <EmptyState
      icon={network ? <WifiOff className="size-6" /> : <AlertTriangle className="size-6" />}
      title={network ? t("connection.disconnected") : t("errors.generic")}
      body={network ? t("errors.network") : status === 404 ? t("errors.notFound") : (error as Error)?.message}
      action={
        onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            {t("common.retry")}
          </Button>
        )
      }
    />
  );
}
