import { ArrowLeft } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { Logo } from "@/components/app/Logo";
import { PreferencesMenu } from "@/components/app/PreferencesMenu";
import { InstrumentAcceptance } from "./InstrumentAcceptance";

/** The acceptance page on its own: from the home screen's instrument list, without a bench. */
export function InstrumentPage() {
  const { defId = "" } = useParams();
  const { t } = useTranslation();
  return (
    <div className="flex h-full flex-col">
      <header className="bg-sidebar flex h-11 shrink-0 items-center gap-3 border-b px-4">
        <Logo />
        <Link
          to="/"
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
        >
          <ArrowLeft className="size-3.5" /> {t("acceptance.backHome")}
        </Link>
        <span className="text-muted-foreground text-xs">{t("acceptance.title")}</span>
        <div className="ml-auto">
          <PreferencesMenu />
        </div>
      </header>
      <main className="min-h-0 flex-1">
        <InstrumentAcceptance key={defId} definitionId={defId} />
      </main>
    </div>
  );
}
