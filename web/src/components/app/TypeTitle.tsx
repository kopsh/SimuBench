import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { type TypeNames, typeAbout, typeName } from "@/lib/component";

/**
 * A component type's name, with its identifier as a grey tag beside it (#1251): the identifier is
 * what a definition writes, the name is what a reader looks for. `tag` replaces the identifier
 * (a placed component's own name).
 */
export function TypeTitle({
  of,
  tag,
  className,
  tagClassName,
}: {
  of: TypeNames;
  tag?: string;
  className?: string;
  tagClassName?: string;
}) {
  const name = typeName(of);
  const shown = tag ?? of.type;
  return (
    <span className={cn("flex min-w-0 items-baseline gap-1.5", className)}>
      <span className="truncate font-medium">{name}</span>
      {shown !== name && <span className={cn("text-muted-foreground num truncate text-[10.5px] font-normal", tagClassName)}>{shown}</span>}
    </span>
  );
}

/**
 * What a component type does: one sentence in the UI's language, and the type's own (English)
 * description folded under 「详细说明」, closed until asked for (#1251).
 */
export function TypeDescription({ of, doc, className }: { of: TypeNames; doc?: string | null; className?: string }) {
  const { t } = useTranslation();
  const about = typeAbout(of);
  const detail = (doc ?? "").trim();
  if (!about && !detail) return null;
  return (
    <div className={cn("space-y-1", className)} data-testid="type-description">
      {about && <p className="text-muted-foreground leading-relaxed">{about}</p>}
      {detail && detail !== about && (
        <details data-testid="type-details">
          <summary className="text-muted-foreground hover:text-foreground cursor-pointer text-[11px] select-none">{t("component.details")}</summary>
          <p className="text-muted-foreground mt-1 text-[11px] leading-relaxed whitespace-pre-line" lang="en">
            {detail}
          </p>
        </details>
      )}
    </div>
  );
}
