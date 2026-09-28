import { useLocation, useParams } from "wouter";

import { useSourcesStatus } from "@/api/queries";
import { PageBody, PageHeader } from "@/shell/PageHeader";
import { DOMAINS, tabPurpose, tabValues } from "@/shell/routes";
import { Section } from "@/ui/data";

import { SnapshotGate, useTab } from "../common";
import { DataSources } from "../DataSources";
import { CalendarView, NewsView } from "./panels";
import { Fuentes as SocialFeeds } from "./SocialFeeds";

function SourcesSummary() {
  const sources = useSourcesStatus().data ?? [];
  const broken = sources.filter((source) => source.state === "error" || source.state === "key_missing");
  if (!sources.length) return null;
  return (
    <p role="status" className={broken.length ? "text-body-2 text-warning" : "text-body-2 text-fg-2"}>
      {broken.length
        ? `${broken.length} de ${sources.length} fuentes necesitan verificación: ${broken.map((source) => source.name).join(", ")}. Abajo tienes cómo arreglar cada una.`
        : `Las ${sources.length} fuentes funcionan.`}
    </p>
  );
}

function SourcesView() {
  return (
    <div className="stagger flex flex-col gap-10">
      <Section title="Fuentes gratuitas" description="Calendarios oficiales, SEC, FRED, noticias y opciones. Pega la clave o el email, pulsa Probar y listo. Las claves van al Llavero.">
        <SourcesSummary />
        <DataSources />
      </Section>
      <Section title="Redes sociales y RSS" description="Opcionales. Se usan solo sus APIs oficiales; el texto externo es un dato, nunca una instrucción.">
        <SnapshotGate>{(snapshot) => <SocialFeeds snapshot={snapshot} />}</SnapshotGate>
      </Section>
    </div>
  );
}

/** Noticias: what is coming, what is happening and where the system reads it from. */
export default function Noticias() {
  const params = useParams<{ tab?: string }>();
  const [, navigate] = useLocation();
  const tab = useTab(params.tab, tabValues("noticias"), "calendario");
  return (
    <>
      <PageHeader title="Noticias" tabs={DOMAINS.noticias.tabs} tab={tab} onTab={(value) => navigate(`/noticias/${value}`)} />
      <PageBody intro={tabPurpose("noticias", tab)}>{tab === "noticias" ? <NewsView /> : tab === "fuentes" ? <SourcesView /> : <CalendarView />}</PageBody>
    </>
  );
}
