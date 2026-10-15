# PhageScope Agent Gateway integration

GAgent exposes the supplied independent gateway through the existing `phagescope`
tool. Use `action="gateway"`, a `gateway_action`, and `gateway_params` containing
that operation's input. Native, strict, Plan execution and code-mode RPC use the
same handler and contract. No content acceptance or approval step is added.

The source contract is the user-supplied `phagescope_agent_gateway.tar.gz`, SHA256
`d0e40c505fb453843153a672768285b419dcfc25b91bef28e34197f0247dcc25`.
It defines 14 data routes and 4 analysis routes, all mapped below. GAgent does
not import or deploy the gateway's Django server, database or Slurm scripts.

## Connection configuration

Configure in the application's existing deployment environment:

```dotenv
PHAGESCOPE_GATEWAY_BASE_URL=https://<issued-gateway-host>
PHAGESCOPE_GATEWAY_DATA_KEY=<issued-data-key>
PHAGESCOPE_GATEWAY_ANALYSIS_KEY=<issued-analysis-key>
```

For independently exposed data/analysis processes, use
`PHAGESCOPE_GATEWAY_DATA_URL` and `PHAGESCOPE_GATEWAY_ANALYSIS_URL` instead of the
common URL. Each accepts its service origin or complete `/agent-api/v1/data`
or `/agent-api/v1/analysis` API root. Keys are sent only in `X-API-Key` for the
matching channel. They are not tool arguments or written into receipts.
Credentials and the actual host are absent from the supplied archive.

`gateway_action="capabilities"` checks local configuration only. Actual remote
checks use `data_health` or `analysis_health`. Missing configuration returns
`configuration_required`, without attempting a legacy website request.

## Operations

| gateway_action | Parameters in gateway_params | Gateway route |
|---|---|---|
| data_health | none | GET data/health/ |
| overview | none; response includes snapshot checked_on | GET data/stats/overview/ |
| datasets | none | GET data/datasets/ |
| phages | accession, dataset, completeness, lifestyle, taxonomy, host, cluster, length_min/max, gc_min/max; at least one filter | GET data/phages/ |
| phage | phage_id (catalog integer id) | GET data/phages/{id}/ |
| phage_proteins | phage_id, optional include_sequence | GET data/phages/{id}/proteins/ |
| phage_features | phage_id, type | GET data/phages/{id}/features/ |
| phage_sequence | phage_id, optional max_bases | GET data/phages/{id}/sequence/ |
| protein | protein_id, optional include_sequence | GET data/proteins/{id}/ |
| hosts | rank, optional name | GET data/hosts/ |
| families | protein_id | GET data/families/ |
| family | family_id | GET data/families/{id}/ |
| family_members | family_id, optional dataset | GET data/families/{id}/members/ |
| structures | protein_id or protein_ids (up to 20) | GET data/structures/available/ |
| analysis_health | none | GET analysis/health/ |
| submit | modules, exactly one of fasta/file_path/accession | POST analysis/jobs/ |
| status | job_id | GET analysis/jobs/{id}/ |
| results | job_id, module | GET analysis/jobs/{id}/results/ |
| save_results | job_id, optional modules | status + results for each module; saves local files |
| capabilities | none | Local discovery, no HTTP call |

All gateway paths have the prefix `/agent-api/v1/`. Paged lists accept `page`
and `pagesize` (up to 50). Operations and accepted fields are defined in
`tool_box/phagescope_gateway_contract.py`, shared by both tool schemas.

## Analysis modules and flow

All 18 supplied modules are available: `annotation`, `quality`, `host`,
`host_cherry`, `lifestyle`, `lifestyle_phatyp`, `viralverify`, `trna`, `crispr`,
`taxonomic_phagcn`, `taxonomic`, `alignment`, `anticrispr`, `arvf`,
`transmembrane`, `terminator`, `tree`, `cluster`.

Duplicates are removed before submission. Modules that require annotation get
one leading `annotation` dependency. Requests remain limited to 12 effective
modules. This resolves the gateway's duplicate-module tracking issue for calls
made through GAgent; it does not change the remote server.

```json
{"action":"gateway","gateway_action":"submit","gateway_params":{"modules":["quality","anticrispr"],"file_path":"input.fasta"}}
```

Submission saves a receipt and returns the remote `job_id` immediately. It does
not wait for the scientific computation. Follow with `status`, then
`save_results`. Remote status is preserved independently from successful HTTP
retrieval and successful local collection. No output-content verifier is run.

POST is never automatically retried. A timeout/lost/invalid successful reply
returns `submission_unknown` and a durable receipt; this is not evidence that
the remote submission failed. The gateway has no job-list/idempotency API to
recover that case automatically. A known job_id is returned even when a disk
error prevents updating its local receipt.

## Files and limits

Saved files are under the active task's `work/phagescope/gateway/` directory
(session/fallback resolver for direct callers). Result collections use fresh
directories so existing files are not overwritten. Tool receipts expose actual
artifact paths, produced_files, task identity and a file manifest with byte
sizes/hashes. Add `save=true` to query parameters to persist a data response;
sequence queries also create a FASTA of the returned sequence.

`save_results` stores module files plus raw responses/status and a manifest.
Collection uses one time budget, retains earlier files on later errors, and
does not extend the existing Plan/tool deadlines. Local cancellation does not
claim cancellation of Slurm work.

The server supplies text previews, not complete file downloads: 1 MiB per
analysis file, up to 20 files for directory modules, 200,000 genome bases and
2,000 protein characters. Remote `truncated` flags are preserved. Truncated
analysis files end in `.preview`; truncated genome files use
`sequence.preview.fasta`. The manifest records missing/truncated files, with
`content_review="not_requested"`. A collection can succeed with missing or
preview files; no scientific acceptance is inferred.

Remote job listing/cancel/full-file download are not exposed by this contract.
Existing legacy website actions and public bulk downloads retain their own
protocol and taskid namespace. GAgent cannot repair server-side quota races,
stale status or partially submitted jobs from this HTTP contract alone.

## Validation and rollout

Automated tests use mock HTTP, temporary task workspaces and fixed model-free
fixtures. They cover every route and analysis module, channel-specific keys,
submission uncertainty, duplicate/dependency handling, saved files/hashes,
truncation, partial collection, cancellation, tool schemas and the real tool
executor/registry. No real Slurm tasks are submitted by tests.

The gateway path is explicitly selected by `action=gateway`; unconfigured
installs continue to use existing website actions. Deploying this client does
not establish that a remote gateway exists or that its credentials work. A live
health/query test is still required once connection details are issued.
