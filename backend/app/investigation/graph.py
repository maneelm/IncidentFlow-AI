import asyncio
import re
from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import END, StateGraph
from langsmith import tracing_context

from app.investigation.analysis import aggregate, parse_output, validate_generation
from app.investigation.errors import InvestigationFailure
from app.investigation.prompts import make_prompt
from app.investigation.providers import Provider, build_provider
from app.investigation.safety import Sanitizer
from app.investigation.store import InvestigationClaim, InvestigationStore
from app.models.investigations import Investigation
from app.repositories.investigation_evidence import EvidenceRepository, evidence_row
from app.repositories.knowledge import KnowledgeRepository
from app.schemas.investigations import ActionOutput, HypothesisOutput

STAGES = (
    "incident_context",
    "evidence_collection",
    "knowledge_retrieval",
    "evidence_analysis",
    "hypotheses",
    "recommendations",
    "validation",
    "final_report",
)


class InvestigationState(TypedDict, total=False):
    investigation_id: str
    incident_id: str
    status: str
    incident_context: dict[str, Any]
    detection_context: dict[str, Any] | None
    evidence_refs: list[str]
    aggregations: dict[str, Any]
    observations: list[dict]
    hypotheses: list[dict]
    actions: list[dict]
    provider_metadata: dict[str, Any]
    current_stage: str
    completed_stages: list[str]
    error_code: str | None
    started_at: str
    generation_feedback: dict


class InvestigationGraph:
    def __init__(
        self, store: InvestigationStore, claim: InvestigationClaim, provider: Provider | None = None
    ) -> None:
        self.store, self.claim, self.provider = store, claim, provider
        self.sanitizer = Sanitizer.from_settings(store.settings)
        builder = StateGraph(InvestigationState)
        for stage in STAGES:
            builder.add_node(stage, self.node(stage))
        builder.set_conditional_entry_point(
            self.next_stage, {stage: stage for stage in STAGES} | {END: END}
        )
        for left, right in zip(STAGES, STAGES[1:], strict=False):
            builder.add_edge(left, right)
        builder.add_edge("final_report", END)
        self.graph = builder.compile()

    @staticmethod
    def next_stage(state: InvestigationState) -> str:
        return next(
            (stage for stage in STAGES if stage not in state.get("completed_stages", [])), END
        )

    def node(self, stage: str):
        async def execute(state: InvestigationState) -> dict:
            await self.store.begin_stage(self.claim, stage)
            updated = dict(state)
            sources = []
            if stage == "incident_context":
                async with self.store.sessions() as session, session.begin():
                    investigation = await session.get(Investigation, self.claim.investigation_id)
                    context, sources = await EvidenceRepository(session, self.sanitizer).context(
                        investigation
                    )
                    updated |= {
                        "investigation_id": str(investigation.id),
                        "incident_id": str(investigation.incident_id),
                        "status": "running",
                        "incident_context": context,
                        "detection_context": context["detection"],
                        "started_at": investigation.started_at.isoformat(),
                        "provider_metadata": {
                            "provider": investigation.provider,
                            "model": investigation.model,
                            "synthetic": investigation.provider == "fake",
                        },
                        "error_code": None,
                    }
            elif stage == "evidence_collection":
                async with self.store.sessions() as session, session.begin():
                    investigation = await session.get(Investigation, self.claim.investigation_id)
                    sources = await EvidenceRepository(session, self.sanitizer).logs(
                        investigation, state["incident_context"]
                    )
            elif stage == "knowledge_retrieval":
                evidence = await self.store.evidence(self.claim.investigation_id)
                terms = [
                    item["provenance"].get("error_code", "")
                    for item in evidence
                    if item["source_type"] == "log"
                ]
                terms += re.findall(
                    r"[A-Za-z][A-Za-z0-9_]{2,39}", state["incident_context"]["title"]
                )
                terms = list(dict.fromkeys(term.lower() for term in terms if term))[:8]
                query = " OR ".join(terms)[:200] or "incident"
                async with self.store.sessions() as session, session.begin():
                    investigation = await session.get(Investigation, self.claim.investigation_id)
                    repository = KnowledgeRepository(session, self.sanitizer)
                    hits = await repository.runbooks(
                        query,
                        UUID(state["incident_context"]["service_id"]),
                        investigation.policy["max_knowledge"],
                        investigation.created_at,
                    )
                    hits += await repository.history(
                        query,
                        UUID(state["incident_context"]["service_id"]),
                        investigation.incident_id,
                        3,
                        investigation.created_at,
                    )
                    for hit in hits:
                        sources.append(
                            evidence_row(
                                investigation.id,
                                hit.source_type,
                                hit.source_id,
                                hit.title,
                                hit.excerpt,
                                "retrieved_knowledge",
                                {
                                    "score": hit.score,
                                    "rank": hit.rank,
                                    "service_relevance": hit.service_relevance,
                                    "document_version": hit.document_version,
                                    "source_timestamp": hit.source_timestamp.isoformat(),
                                },
                                self.sanitizer,
                            )
                        )
            elif stage == "evidence_analysis":
                evidence = await self.store.evidence(self.claim.investigation_id)
                aggregation = aggregate(evidence)
                logs = [
                    item["reference"]
                    for item in evidence
                    if item["source_type"] == "log" and not item["quarantined"]
                ]
                observations = [
                    {
                        "statement": (
                            f"Selected usable log count: {aggregation['selected_logs']}; "
                            f"levels: {aggregation['levels']}."
                        ),
                        "evidence_ids": logs or [f"incident:{state['incident_id']}"],
                    }
                ]
                if state.get("detection_context"):
                    detection = state["detection_context"]
                    observations.append(
                        {
                            "statement": (
                                "Stored detection threshold witness count: "
                                f"{detection['matching_count']} (a lower bound)."
                            ),
                            "evidence_ids": [f"detection:{detection['id']}"],
                        }
                    )
                if logs:
                    observations.append(
                        {
                            "statement": (
                                f"Selected service count: {aggregation['affected_services']}. "
                                f"Source time range: {aggregation['first_source_timestamp']} "
                                f"to {aggregation['last_source_timestamp']}."
                            ),
                            "evidence_ids": logs,
                        }
                    )
                for code, count in list(aggregation["error_codes"].items())[:5]:
                    observations.append(
                        {
                            "statement": f"Selected error code {code}: {count} records.",
                            "evidence_ids": [
                                item["reference"]
                                for item in evidence
                                if item["source_type"] == "log"
                                and not item["quarantined"]
                                and item["provenance"].get("error_code") == code
                            ],
                        }
                    )
                updated |= {"aggregations": aggregation, "observations": observations}
            elif stage == "hypotheses":
                updated["hypotheses"] = (
                    (await self.generate("hypotheses", HypothesisOutput, state))["hypotheses"]
                    if state["aggregations"]["sufficient"]
                    else []
                )
            elif stage == "recommendations":
                updated["actions"] = (
                    (await self.generate("actions", ActionOutput, state))["actions"]
                    if state["aggregations"]["sufficient"]
                    else [
                        {
                            "description": (
                                "Collect additional service logs and inspect approved "
                                "read-only observability views."
                            ),
                            "rationale": (
                                "The selected evidence is insufficient for causal reasoning."
                            ),
                            "evidence_ids": [f"incident:{state['incident_id']}"],
                            "risk": "read_only",
                            "requires_human_review": True,
                        }
                    ]
                )
            elif stage == "validation":
                evidence = await self.store.evidence(self.claim.investigation_id)
                if state["hypotheses"]:
                    validate_generation(
                        HypothesisOutput(hypotheses=state["hypotheses"]), evidence, self.sanitizer
                    )
                validate_generation(
                    ActionOutput(actions=state["actions"]), evidence, self.sanitizer
                )
            elif stage == "final_report":
                return await self.store.finish(self.claim, updated)
            if sources:
                updated["evidence_refs"] = [
                    *state.get("evidence_refs", []),
                    *(row.reference for row in sources),
                ]
            return await self.store.checkpoint(self.claim, stage, updated, sources)

        return execute

    async def generate(self, task: str, schema: type, state: InvestigationState) -> dict:
        evidence = await self.store.evidence(self.claim.investigation_id)
        system, payload, reservation = make_prompt(
            schema,
            task,
            state["incident_context"],
            state["aggregations"],
            evidence,
            state.get("hypotheses", []),
            state.get("generation_feedback"),
        )
        async with self.store.sessions() as session, session.begin():
            investigation = await session.get(Investigation, self.claim.investigation_id)
            provider_name, model, timeout = (
                investigation.provider,
                investigation.model,
                investigation.policy["timeout_seconds"],
            )
        provider = self.provider or build_provider(
            provider_name, model, self.store.settings, timeout
        )
        max_output, _ = await self.store.reserve(self.claim, reservation)
        try:
            # No session or database transaction is open across this await.
            async with asyncio.timeout(timeout):
                response = await provider.generate(task, system, payload, schema, max_output)
        except TimeoutError:
            raise InvestigationFailure("provider_timeout", retryable=True) from None
        except InvestigationFailure as exc:
            exc.diagnostics = exc.diagnostics | {
                "prompt_bytes_reserved": reservation,
                "max_completion_tokens": max_output,
            }
            raise
        if len(response.content.encode()) > 16384:
            raise InvestigationFailure("output_size_exceeded")
        await self.store.usage(
            self.claim, response.input_tokens, response.output_tokens, reservation, max_output
        )
        try:
            if response.finish_reason != "stop":
                reason = response.finish_reason
                # Count usage before rejecting a truncated response. Identical
                # calls cannot resolve a refusal or an exhausted per-call cap.
                raise InvestigationFailure(
                    "incomplete_model_output",
                    retryable=reason not in {"length", "content_filter"},
                    diagnostics={
                        "finish_reason": reason
                        if reason in {"length", "content_filter"}
                        else "other"
                    },
                )
            output = parse_output(schema, response.content)
            validate_generation(output, evidence, self.sanitizer)
        except InvestigationFailure as exc:
            exc.diagnostics = (
                response.diagnostics
                | exc.diagnostics
                | {
                    "prompt_bytes_reserved": reservation,
                    "max_completion_tokens": max_output,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                }
            )
            raise
        return output.model_dump(mode="json")

    async def run(self) -> InvestigationState:
        checkpoint = await self.store.load(self.claim)
        # Disable external LangSmith tracing even if inherited process env enables it.
        with tracing_context(enabled=False):
            return await self.graph.ainvoke(
                checkpoint, config={"callbacks": [], "recursion_limit": 16}
            )
