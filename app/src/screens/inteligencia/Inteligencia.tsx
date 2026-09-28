import { useLocation, useParams } from "wouter";

import { PageBody, PageHeader } from "@/shell/PageHeader";
import { DOMAINS, tabPurpose, tabValues } from "@/shell/routes";

import { Cambios } from "../aprendizaje/Cambios";
import { Memoria } from "../aprendizaje/Memoria";
import { SnapshotGate, useTab } from "../common";
import { Agentes } from "./Agentes";
import { Modelos } from "./Modelos";

/** La IA: sus agentes, sus modelos, cómo aprende y qué recuerda. */
export default function Inteligencia() {
  const params = useParams<{ tab?: string; item?: string }>();
  const [, navigate] = useLocation();
  const tab = useTab(params.tab, tabValues("inteligencia"), "agentes");

  return (
    <>
      <PageHeader title="Inteligencia" tabs={DOMAINS.inteligencia.tabs} tab={tab} onTab={(value) => navigate(`/inteligencia/${value}`)} />
      <PageBody intro={tabPurpose("inteligencia", tab)}>
        {tab === "memoria" ? (
          <Memoria />
        ) : (
          <SnapshotGate>
            {(snapshot) =>
              tab === "modelos" ? (
                <Modelos snapshot={snapshot} />
              ) : tab === "aprendizaje" ? (
                <Cambios snapshot={snapshot} />
              ) : (
                <Agentes snapshot={snapshot} selected={params.item ? decodeURIComponent(params.item) : null} />
              )
            }
          </SnapshotGate>
        )}
      </PageBody>
    </>
  );
}
