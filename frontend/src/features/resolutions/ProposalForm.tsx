import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../../lib/api/client";
import { useApi, useWrite } from "../../lib/api/query";
import { label, uuid } from "../../lib/formatting";
import type {
  CatalogAction,
  Evidence,
  Incident,
  Investigation,
  Page,
  Proposal,
  ProposalCreate,
  Report,
  SyntheticState,
} from "../../types/models";
import {
  Badge,
  Button,
  Dialog,
  ErrorPanel,
  Field,
  Loading,
  Notice,
  Submit,
} from "../../components/ui";

export function ProposalForm({
  incident: initialIncident,
  close,
  initialInvestigation = "",
  recommendation = 0,
}: {
  incident: Incident;
  close: () => void;
  initialInvestigation?: string;
  recommendation?: number;
}) {
  const [incident] = useState(initialIncident);
  const navigate = useNavigate();
  const [clientId] = useState(uuid);
  const [actionId, setActionId] = useState("");
  const [investigationId, setInvestigationId] = useState(initialInvestigation);
  const [refs, setRefs] = useState<string[]>([]);
  const [mapRecommendation, setMapRecommendation] = useState(false);
  const [unknownBody, setUnknownBody] = useState<ProposalCreate | null>(null);
  const catalog = useApi<CatalogAction[]>("/action-catalog");
  const state = useApi<SyntheticState>(
    `/services/${incident.service_id}/synthetic-state`,
  );
  const investigations = useApi<Page<Investigation>>(
    `/incidents/${incident.id}/investigations`,
    { limit: 100, offset: 0 },
  );
  const report = useApi<Report>(
    `/investigations/${investigationId}/report`,
    undefined,
    { enabled: !!investigationId },
  );
  const evidence = useApi<Page<Evidence>>(
    `/investigations/${investigationId}/evidence`,
    { limit: 100, offset: 0 },
    { enabled: !!investigationId },
  );
  const selected = catalog.data?.find((item) => item.id === actionId);
  const selectedRecommendation =
    report.data?.recommended_actions[recommendation];
  const write = useWrite<Proposal, ProposalCreate>((body) =>
    api.post(`/incidents/${incident.id}/resolution-proposals`, body),
  );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    if (!state.data || !selected) return;
    const evidenceRefs = mapRecommendation
      ? (selectedRecommendation?.evidence_ids ?? [])
      : refs;
    const body: ProposalCreate = unknownBody ?? {
      client_request_id: clientId,
      expected_incident_version: incident.version,
      action_id: selected.id,
      parameters: {
        state_id: state.data.id,
        expected_state_version: state.data.version,
      },
      explanation: String(values.get("explanation")),
      origin: mapRecommendation ? "investigation" : "human",
      ...(investigationId
        ? {
            investigation_id: investigationId,
            evidence_refs: evidenceRefs,
            ...(mapRecommendation
              ? { recommendation_index: recommendation }
              : {}),
          }
        : { evidence_refs: [] }),
    };
    try {
      const result = await write.mutateAsync(body);
      close();
      navigate(`/resolutions/${result.id}`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 0) setUnknownBody(body);
    }
  }
  return (
    <Dialog title="Create resolution proposal" onClose={close}>
      <Notice tone="warning">
        Every action requires human approval. Synthetic changes need a different
        approver. This form cannot execute an action.
      </Notice>
      <ErrorPanel error={catalog.error} />
      <ErrorPanel error={report.error} />
      <ErrorPanel error={evidence.error} />
      {state.isPending && <Loading />}
      {state.error && (
        <Notice tone="warning">
          Synthetic state is unavailable. Ask an admin to initialize it on the{" "}
          <Link to={`/services/${incident.service_id}`}>service page</Link>.
        </Notice>
      )}
      <ErrorPanel error={write.error} />
      {unknownBody && (
        <Notice tone="warning">
          The create outcome is unknown. Verify the proposal list before
          retrying. Any manual retry below sends exactly the same request ID and
          payload.
        </Notice>
      )}
      <form onSubmit={submit}>
        <fieldset disabled={write.isPending || !!unknownBody}>
          <Field
            label="Catalog action"
            hint="Choose explicitly. No recommendation text is converted to an action."
          >
            <select
              required
              value={actionId}
              onChange={(event) => {
                setActionId(event.target.value);
                setMapRecommendation(false);
              }}
            >
              <option value="">Select an allowlisted action</option>
              {catalog.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {label(item.id)}
                </option>
              ))}
            </select>
          </Field>
          {selected && (
            <>
              <div className="actions">
                <Badge value={selected.risk} />
                <Badge value="synthetic" synthetic />
              </div>
              <p className="small muted">
                {selected.description}{" "}
                {selected.distinct_approver_required
                  ? "A second engineer or administrator must approve."
                  : "Explicit human review is still required."}
              </p>
            </>
          )}
          {state.data && (
            <dl className="detail-grid">
              <dt>Target service</dt>
              <dd className="mono wrap">{incident.service_id}</dd>
              <dt>Synthetic state</dt>
              <dd className="mono wrap">{state.data.id}</dd>
              <dt>State version</dt>
              <dd>{state.data.version}</dd>
              <dt>Incident version</dt>
              <dd>{incident.version}</dd>
            </dl>
          )}
          <Field label="Investigation provenance (optional)">
            <select
              value={investigationId}
              onChange={(event) => {
                setInvestigationId(event.target.value);
                setRefs([]);
                setMapRecommendation(false);
              }}
            >
              <option value="">
                Human proposal without investigation provenance
              </option>
              {initialInvestigation &&
                !investigations.data?.items.some(
                  (item) => item.id === initialInvestigation,
                ) && (
                  <option value={initialInvestigation}>
                    {initialInvestigation}
                  </option>
                )}
              {investigations.data?.items
                .filter((item) =>
                  ["completed", "inconclusive"].includes(item.status),
                )
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.id.slice(0, 8)} · {item.status}
                  </option>
                ))}
            </select>
          </Field>
          {investigationId && (
            <>
              <p className="small muted">
                Choose at least one usable source. Quarantined sources cannot be
                selected.
              </p>
              <div className="evidence-picker">
                {evidence.data?.items
                  .filter((item) => !item.quarantined)
                  .map((item) => (
                    <label className="checkbox" key={item.reference}>
                      <input
                        type="checkbox"
                        disabled={
                          mapRecommendation ||
                          (!refs.includes(item.reference) && refs.length >= 32)
                        }
                        checked={
                          mapRecommendation
                            ? !!selectedRecommendation?.evidence_ids.includes(
                                item.reference,
                              )
                            : refs.includes(item.reference)
                        }
                        onChange={(event) =>
                          setRefs(
                            event.target.checked
                              ? [...refs, item.reference]
                              : refs.filter((ref) => ref !== item.reference),
                          )
                        }
                      />
                      <span>
                        {item.label}
                        <small className="mono block muted">
                          {item.reference}
                        </small>
                      </span>
                    </label>
                  ))}
              </div>
              {selected &&
                !selected.mutates_synthetic_state &&
                selectedRecommendation && (
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={mapRecommendation}
                      onChange={(event) =>
                        setMapRecommendation(event.target.checked)
                      }
                    />
                    Explicitly link recommendation {recommendation + 1} and its
                    cited sources to this read-only action
                  </label>
                )}
              {selected?.mutates_synthetic_state && (
                <Notice>
                  This recovery proposal is human authored. AI recommendations
                  do not authorize synthetic changes.
                </Notice>
              )}
            </>
          )}
          <Field label="Human explanation">
            <textarea
              name="explanation"
              required
              minLength={10}
              maxLength={2000}
              rows={4}
              placeholder="Explain why you propose this specific synthetic action and what you reviewed."
            />
          </Field>
        </fieldset>
        <p className="small mono muted wrap">Client request ID: {clientId}</p>
        <div className="form-footer">
          <Button type="button" onClick={close}>
            Cancel
          </Button>
          <Submit
            pending={
              write.isPending ||
              !state.data ||
              !selected ||
              (!!investigationId &&
                !(mapRecommendation
                  ? selectedRecommendation?.evidence_ids.length
                  : refs.length))
            }
          >
            {unknownBody
              ? "Retry identical request"
              : "Create immutable proposal"}
          </Submit>
        </div>
      </form>
    </Dialog>
  );
}
