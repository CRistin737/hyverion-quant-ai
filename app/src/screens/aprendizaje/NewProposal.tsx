import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { SNAPSHOT_KEY, useApi } from "@/api/provider";
import type { Agent } from "@/api/types";
import { agentName, labErrorMessage } from "@/i18n/labels";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Field, Input, Select } from "@/ui/form";
import { Dialog } from "@/ui/overlay";
import { useToast } from "@/ui/toast";

const TEXTAREA = cn(
  "w-full resize-none rounded-sm bg-surface-2 px-2.5 py-2 text-body text-fg placeholder:text-fg-muted",
  "shadow-[inset_0_0_0_1px_var(--border-default)] hover:shadow-[inset_0_0_0_1px_var(--border-strong)]",
  "focus-visible:outline-2 focus-visible:outline-offset-1",
);

const lines = (value: string) =>
  value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

export interface NewProposalInput {
  agent: string;
  current_version: string;
  candidate_version: string;
  reason: string;
  evidence: string[];
  affected_rules: string[];
  expected_improvement: string;
  risk: string;
  candidate_spec: Record<string, string>;
}

/** Validation mirrors ChangeProposalRequest so the core never sees an invalid request. */
export function proposalErrors(input: NewProposalInput): string[] {
  const errors: string[] = [];
  if (!input.agent) errors.push("Elige el agente.");
  if (!input.candidate_version.trim()) errors.push("Indica la versión candidata.");
  if (input.candidate_version.trim() === input.current_version.trim()) errors.push("La versión candidata debe ser distinta de la actual.");
  if (input.reason.trim().length < 10) errors.push("Explica el motivo (al menos 10 caracteres).");
  if (!input.evidence.length) errors.push("Añade al menos una evidencia.");
  if (!input.affected_rules.length) errors.push("Indica al menos una regla afectada.");
  if (!input.expected_improvement.trim()) errors.push("Describe la mejora esperada.");
  if (!input.risk.trim()) errors.push("Describe el riesgo del cambio.");
  if (!input.candidate_spec.change?.trim()) errors.push("Describe el cambio propuesto.");
  return errors;
}

/**
 * Human-authored change proposal. It only creates a PROPOSED record for review;
 * nothing is deployed and no prompt or configuration is edited.
 */
export function NewProposalDialog({ open, onOpenChange, agents }: { open: boolean; onOpenChange: (open: boolean) => void; agents: Agent[] }) {
  const { client } = useApi();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [agent, setAgent] = useState("");
  const [candidate, setCandidate] = useState("");
  const [reason, setReason] = useState("");
  const [change, setChange] = useState("");
  const [evidence, setEvidence] = useState("");
  const [rules, setRules] = useState("");
  const [improvement, setImprovement] = useState("");
  const [risk, setRisk] = useState("");
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (!open) return;
    setAgent("");
    setCandidate("");
    setReason("");
    setChange("");
    setEvidence("");
    setRules("");
    setImprovement("");
    setRisk("");
    setSubmitted(false);
  }, [open]);

  const current = agents.find((item) => item.agent_id === agent)?.version ?? "";
  const input: NewProposalInput = useMemo(
    () => ({
      agent,
      current_version: current,
      candidate_version: candidate.trim(),
      reason: reason.trim(),
      evidence: lines(evidence),
      affected_rules: lines(rules),
      expected_improvement: improvement.trim(),
      risk: risk.trim(),
      candidate_spec: { change: change.trim() },
    }),
    [agent, current, candidate, reason, evidence, rules, improvement, risk, change],
  );
  const errors = proposalErrors(input);

  const create = useMutation({
    mutationFn: () => client.post<Record<string, unknown>>("/api/v1/learning/proposals", input),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["changes"] }),
        queryClient.invalidateQueries({ queryKey: SNAPSHOT_KEY }),
      ]);
      toast({ tone: "success", title: "Propuesta creada", description: "Quedó en «Propuesta» para revisión. Nada se desplegó." });
      onOpenChange(false);
    },
  });

  const submit = () => {
    setSubmitted(true);
    if (!errors.length) create.mutate();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      width={560}
      title="Nueva propuesta de cambio"
      description="Queda registrada para revisión con su evidencia. No modifica prompts, configuración ni código."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button variant="primary" loading={create.isPending} onClick={submit}>
            Crear propuesta
          </Button>
        </>
      }
    >
      <div className="flex max-h-[60vh] flex-col gap-4 overflow-y-auto pr-1">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Agente">
            {(id, describedBy) => (
              <Select
                id={id}
                describedBy={describedBy}
                value={agent}
                onValueChange={setAgent}
                options={agents.map((item) => ({ value: item.agent_id, label: agentName(item.agent_id) }))}
              />
            )}
          </Field>
          <Field label="Versión candidata" hint={current ? `Actual: ${current}` : undefined}>
            {(id, describedBy) => <Input id={id} aria-describedby={describedBy} value={candidate} onChange={(e) => setCandidate(e.target.value)} placeholder="p. ej. 1.1.0" maxLength={80} />}
          </Field>
        </div>
        <Field label="Cambio propuesto" hint="Qué cambiaría exactamente en la especificación del agente.">
          {(id, describedBy) => <textarea id={id} aria-describedby={describedBy} rows={3} maxLength={2000} value={change} onChange={(e) => setChange(e.target.value)} className={TEXTAREA} />}
        </Field>
        <Field label="Motivo">
          {(id, describedBy) => <textarea id={id} aria-describedby={describedBy} rows={2} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} className={TEXTAREA} />}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Evidencia" hint="Una por línea (operaciones, métricas, notas).">
            {(id, describedBy) => <textarea id={id} aria-describedby={describedBy} rows={3} value={evidence} onChange={(e) => setEvidence(e.target.value)} className={TEXTAREA} />}
          </Field>
          <Field label="Reglas afectadas" hint="Una por línea.">
            {(id, describedBy) => <textarea id={id} aria-describedby={describedBy} rows={3} value={rules} onChange={(e) => setRules(e.target.value)} className={TEXTAREA} />}
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Mejora esperada">
            {(id, describedBy) => <textarea id={id} aria-describedby={describedBy} rows={2} maxLength={1000} value={improvement} onChange={(e) => setImprovement(e.target.value)} className={TEXTAREA} />}
          </Field>
          <Field label="Riesgo">
            {(id, describedBy) => <textarea id={id} aria-describedby={describedBy} rows={2} maxLength={1000} value={risk} onChange={(e) => setRisk(e.target.value)} className={TEXTAREA} />}
          </Field>
        </div>
        {submitted && errors.length ? (
          <ul className="flex flex-col gap-0.5 text-caption text-danger" role="alert">
            {errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        ) : null}
        {create.error ? (
          <p className="text-caption text-danger" role="alert">
            {labErrorMessage(create.error.message)}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
