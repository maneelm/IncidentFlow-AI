# RAG storage and retrieval

Retrieval-Augmented Generation here means selecting real operational evidence from PostgreSQL before reasoning. It does not require an embedding API. Search combines lexical full-text results, service association and explicit historical detection-code matching; scores are ranking signals, not probabilities.

## Controlled documents

Admins POST trusted Markdown/plain-text content as JSON or upload a UTF-8 `.md`/`.txt` file. File extensions and MIME types are allowlisted; decoding is strict UTF-8 with optional BOM. Content accepts 1–64000 characters and at most 131072 UTF-8 bytes. Upload reads at most 131073 bytes before rejecting oversize content. An ASGI guard caps total runbook request bodies at 262144 bytes, including multipart/chunked input, before parsing. Documents are never fetched from a URL, interpreted as Python, executed, or written to a caller-selected path.

Content, titles and tags are redacted before storage using the same known-secret/token screening boundary used for investigation evidence. The content hash is SHA-256 of the stored normalized/redacted text, not a promise that the upload was retained byte-for-byte. Service association is optional and fixed after creation; tags accept at most ten distinct 1–40-character values. Runbook metadata and current version live in `runbooks`.

Each replacement creates an immutable-by-public-interface `runbook_versions` row containing title/service/tags/format/content/hash/author/time, plus new `runbook_chunks`. PATCH requires expected_version; concurrent stale edits return 409. Reindex also creates a new version, preserving old chunk UUIDs and references. There are no public document/version/chunk deletion or old-version mutation routes. Database owners are outside that public immutability boundary.

## Deterministic chunking

`services/runbooks.chunks` splits stored text into at most 1800-character chunks with 200-character overlap. Offsets identify exact `[start_char,end_char)` slices in the stored version. Each chunk has UUID, ordinal, version FK, text, offsets, hash and search text containing title/tags/body. This is a simple character splitter, not semantic or Markdown-heading segmentation. It is bounded and reproducible, and old chunks stay available after reindexing.

GET `/runbook-chunks/{id}` exposes text/offset/hash/version ID. GET `/runbooks/{id}/versions/{version}` retrieves the source version. Investigation evidence records the selected chunk UUID and document version, so a later replacement does not silently change what an old investigation cited.

## PostgreSQL full-text search

GIN indexes cover `to_tsvector('simple', search_text)` on chunks, incident title+description, and resolution-note content. No `pgvector` extension, external embedding provider, vector dimensions or custom vector engine is used.

Runbook search uses parameterized `websearch_to_tsquery`, bounded queries of 2–200 characters and limits 1–20. The graph constructs at most eight distinct safe lexical terms from observed error codes and the incident title, joined with OR and bounded to 200 characters. This increases recall without letting evidence inject SQL syntax or select arbitrary tables.

For a service-specific request, eligible runbooks belong to that service or are global. Same-service hits rank first, then `ts_rank_cd` descending, then document UUID and chunk ordinal for deterministic ties. Public search uses current versions. Investigation retrieval chooses the latest document version ingested at or before the investigation request timestamp. This prevents a post-request replacement from changing a queued investigation's selected document version.

Search hits include source type/UUID, title, bounded excerpt, score, rank, service relevance, source timestamp and optional document version. Excerpts are at most 600 characters. A high lexical score is not a confidence estimate and does not prove that a runbook applies.

## Historical cases and resolution notes

`KnowledgeRepository.history` excludes the current incident and selects resolved/closed cases. It matches lexical title/description terms or an exact detection error code extracted from the search terms. Same-service hits rank first; lexical score, resolution recency and UUID break ties. Candidate excerpts include bounded descriptions, available transition reasons and detection code. Resolution notes are separate human-authored append-only records accepted only for resolved/closed incidents; they have their own text index and are included as independently cited hits.

The graph requests at most three historical/note hits. Public similar search accepts at most twenty. When merging notes and incidents, service relevance, score and UUID establish a deterministic order and rank. A similar historical symptom is supporting context, not evidence that the current case has the same cause. No LLM writes resolution notes or confirms resolution.

Historical incident metadata can be edited by authorized humans; a selected bounded excerpt is stored in the investigation evidence set at collection time. That snapshot avoids changing a retry's text. It is not a reconstruction of every historical database field at the exact original request instant. Source timestamps and IDs preserve provenance, and original audit history remains available separately.

## Dedicated log collection

Phase 2's threshold witness method is deliberately not reused as a complete collector. `EvidenceRepository` loads the incident/service, up to eight relevant audit events and its detection occurrence/rule snapshot. It reconstructs the original threshold witnesses using the stored window/filter, association table and the same deterministic receipt/source-ID ordering. Up to 1000 UUIDs may be considered for provenance, but only the configured selected-log cap can enter evidence.

With default max_logs=20, up to ten original witnesses are selected, then up to five additional linked logs. Remaining slots contain contextual records, excluding duplicates and original witnesses already considered. Context favors matching trace IDs, request IDs and error codes, then recent receipt time/UUID. It searches the same service and, when present, exact witness trace IDs across services. This is bounded correlation, not unrestricted service-wide export.

The collection interval starts five minutes before the original detection window (or manual incident creation). It ends at the earlier of request time and fifteen minutes after the trigger/manual incident creation. Detection uses receipt times; source timestamps remain distinct provenance fields. Delayed investigations may exclude much later incident activity; this is an explicit fixed-window trade-off. Manual incidents without relevant logs remain inconclusive.

Roles distinguish `original_witness`, `linked_evidence`, `context_log`, `incident_context`, `service_context`, `audit_history`, `detection_context` and `retrieved_knowledge`. Log metadata is restricted further to selected operational fields, such as latency/status/pool sizes and synthetic markers; arbitrary metadata is not sent wholesale to a hosted provider.

Each evidence row contains a stable typed source reference, a real FK, redacted bounded excerpt/label, collection/retrieval provenance and quarantine flag. Source type/ref checks and report-citation composite FKs prevent nonexistent sources from quietly entering the evidence relation. Graph checkpoints keep references and aggregates rather than duplicating full raw log sets at every stage.

## Limits

Lexical retrieval misses synonyms and semantically related documents without overlapping terms. Character chunks may split an explanation mid-paragraph. Historical matches may be outdated or misleading. There is no semantic embedding index or automatic corpus synchronization. These limits are exposed in reports and documentation rather than disguised as calibrated relevance or proven causality.
