import type { components, paths } from "./openapi";
export type Schema<K extends keyof components["schemas"]> =
  components["schemas"][K];
export type User = Schema<"UserRead">;
export type Service = Schema<"ServiceRead">;
export type Incident = Schema<"IncidentRead">;
export type IncidentCreate = Schema<"IncidentCreate">;
export type IncidentUpdate = Schema<"IncidentUpdate">;
export type Log = Schema<"LogRead">;
export type Rule = Schema<"RuleRead">;
export type Detection = Schema<"DetectionRead">;
export type Investigation = Schema<"InvestigationRead">;
export type Report = Schema<"ReportRead">;
export type Evidence = Schema<"EvidenceRead">;
export type Step = Schema<"StepRead">;
export type Runbook = Schema<"RunbookRead">;
export type Document = Schema<"RunbookDocument">;
export type Proposal = Schema<"ProposalRead">;
export type ProposalCreate = Schema<"ProposalCreate">;
export type CatalogAction = Schema<"CatalogRead">;
export type SyntheticState = Schema<"SyntheticStateRead">;
export type Execution = Schema<"ExecutionRead">;
export type Approval = Schema<"ApprovalRead">;
export type ResolutionEvent = Schema<"ResolutionEventRead">;
export type TimelinePage = Schema<"TimelinePage">;
export type Overview = Schema<"OverviewRead">;
export type Assignee = Schema<"AssigneeRead">;
export type IncidentFilters = NonNullable<
  paths["/api/v1/incidents"]["get"]["parameters"]["query"]
>;
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
export type Params = Record<
  string,
  string | number | boolean | null | undefined
>;
