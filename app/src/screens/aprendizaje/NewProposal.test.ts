import { describe, expect, it } from "vitest";

import { proposalErrors, type NewProposalInput } from "./NewProposal";

const valid: NewProposalInput = {
  agent: "strategy",
  current_version: "1.0.0",
  candidate_version: "1.1.0",
  reason: "Demasiadas entradas en rango lateral",
  evidence: ["12 operaciones perdedoras en régimen lateral"],
  affected_rules: ["Respect regime eligibility"],
  expected_improvement: "Menos entradas falsas",
  risk: "Menos operaciones en tendencias débiles",
  candidate_spec: { change: "Exigir régimen de tendencia confirmado" },
};

describe("new change proposal validation", () => {
  it("accepts a complete proposal", () => {
    expect(proposalErrors(valid)).toEqual([]);
  });

  it("requires evidence, rules, a different version and a described change", () => {
    const errors = proposalErrors({ ...valid, candidate_version: "1.0.0", evidence: [], affected_rules: [], candidate_spec: { change: " " } });
    expect(errors).toHaveLength(4);
  });

  it("requires an agent and a meaningful reason", () => {
    expect(proposalErrors({ ...valid, agent: "", reason: "corto" })).toHaveLength(2);
  });
});
