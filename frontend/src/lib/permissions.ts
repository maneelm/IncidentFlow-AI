import type { Proposal, User } from "../types/models";
export const canWrite = (user: User | null) => !!user && user.role !== "viewer";
export const canAdmin = (user: User | null) => user?.role === "admin";
export function reviewEligibility(user: User | null, proposal: Proposal) {
  if (!canWrite(user))
    return { allowed: false, reason: "Your role has read-only access." };
  if (proposal.status !== "pending_approval")
    return {
      allowed: false,
      reason: "Only pending proposals can be reviewed.",
    };
  if (
    proposal.approval_deadline &&
    new Date(proposal.approval_deadline).getTime() <= Date.now()
  )
    return {
      allowed: false,
      reason:
        "Approval deadline has passed. Refresh the proposal for its persisted status.",
    };
  if (
    proposal.risk === "synthetic_mutation" &&
    proposal.requested_by === user?.id
  )
    return {
      allowed: false,
      reason:
        "A different engineer or administrator must review this synthetic change. Administrators have no self-approval override.",
    };
  return {
    allowed: true,
    reason:
      "Review the exact target, evidence and immutable parameters before deciding.",
  };
}
export const transitions: Record<string, string[]> = {
  triggered: ["investigating"],
  investigating: ["mitigated", "resolved"],
  mitigated: ["resolved"],
  resolved: ["closed", "investigating"],
  closed: ["investigating"],
};
