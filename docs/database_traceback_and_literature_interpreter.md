# Database tracebacks and literature-grounded interpretation

## Purpose and separation of responsibilities

The evidence inspector now has three deliberately separate layers:

1. **Database-native provenance** answers which source record and source fields
   produced a STRING, OmniPath, BioGRID, or closure contribution.
2. **Bayesian reconstruction** remains the authoritative arithmetic ledger. It
   reports the applied BF, evidence weight, weighted log-odds contribution, and
   reconstructed posterior.
3. **Optional literature interpretation** uses a reasoning model with web search
   to assess biological context and apparent novelty and to explain layers 1–2.
   This downstream prose cannot add evidence, change a BF, change a posterior,
   or rerank a path.

This separation prevents a generated narrative from being mistaken for a new
measurement or silently changing the scientific result.

## Database-native traceback

Tracebacks are created on demand when a scientist inspects an edge. They are
read from the exact frozen project files that supplied the graph.

### STRING v12

The panel exposes the mapped STRING protein identifiers and all seven detailed
evidence channels: experiments, curated databases, text mining, coexpression,
genomic neighborhood, gene fusion, and phylogenetic co-occurrence. It also
shows STRING's combined score and the application's transformation:

`BF = odds(combined score) / odds(0.041 × reference multiplier)`

Only the combined score is converted to the graph's STRING BF. Component
channels are explanatory provenance and are **not multiplied again**. For a
seed edge they come from `string_supported_undirected_edges.tsv`; a dynamically
added pair is resolved to STRING IDs and looked up in the frozen v12 detailed
links file. The result explicitly warns that STRING associations are not
automatically direct physical interactions.

### OmniPath

The panel returns every matching directed source record from the frozen mouse
core post-translational snapshot, including source and target accessions and
symbols, stimulation/inhibition flags, consensus direction/sign flags,
contributing resources, references, and curation effort. The undirected edge
stream uses the maximum curation effort and the configured Tq/reference scale.
Native direction and sign are retained for audit even though edge existence is
integrated as an undirected hypothesis; traversal direction is handled later by
the path-direction layer.

### BioGRID

The panel reports the mapped source pair, source species, source symbols,
Entrez and BioGRID identifiers, experimental systems, publication identifiers,
direct-contact/co-complex/HuRI flags, low-throughput status, and mapping route.
It also distinguishes native mouse records from human or rat orthology
projections. Direct-contact and co-complex reports receive the same one-time
configured BF. Publication and method counts are provenance descriptors, not
additional multipliers; overlapping assay tiers are counted once.

### Shared-partner and scaffold closure

Closure is labeled explicitly as a derived rule. Its traceback reports the
shared physical partner and qualifying support count from the run audit. It is
never described as a direct database-reported interaction between the two
endpoints.

## Literature-grounded context and novelty interpreter

After inspecting a node or edge, the user supplies two pieces of scientific
context:

- the cell/tissue context, such as collecting-duct principal cells across CCD,
  OMCD, and IMCD;
- the signaling purpose, such as vasopressin-regulated AQP2 signaling and water
  transport.

The application sends a compact, frozen evidence ledger and those two context
fields to the selected OpenAI-compatible Responses API only after the user
presses **Research the displayed network**. The request requires hosted web search and strict
structured output. It asks the model to search exact symbols, aliases where
apparent, both directions for edges, the specified cell type, and the signaling
purpose. Primary research and authoritative databases are preferred.

Each result must use one of these cautious classifications:

- `known_in_exact_context`
- `known_in_related_context`
- `database_supported_context_unknown`
- `plausible_novel_candidate`
- `unsupported_or_conflicting`
- `insufficient_evidence`

The word *novel* is therefore not inferred simply because a search returned no
paper. The output must state that no direct report was found **in that search**,
keep exact-context evidence separate from related-context plausibility, cite
claims with URLs, and expose conflicts, caveats, and proposed follow-up.

The GUI audits the complete union of nodes and edges in the number of top paths
currently displayed in the network panel. It freezes every unique hypothesis,
builds its evidence ledger, attaches non-neutral STRING/OmniPath/BioGRID or
closure tracebacks, and submits bounded groups of six hypotheses for web
research. One final model call synthesizes the item-level results at pathway
level. The GUI shows the node count, edge count, and estimated number of paid
search batches before submission, then reports progress asynchronously.

The result is cached under the immutable run directory using a hash of all
ledgers, the visible path scope, biological context, model, and reasoning
level. Repeating the same request returns the cache unless **Ignore cached
audit** is checked. Every node/edge report and the network synthesis are
included in the standalone session HTML, but the API key is never retained.

## Configuration

The preferred interactive method is the password-style **OpenAI / Azure API
key** box in the result panel. The browser sends that value once in the
`X-OpenAI-API-Key` request header and clears the field after the server accepts
the job. The backend passes it directly to the worker in memory. It is never
put in the submitted workflow configuration, URL, run directory, cache, log,
Git, or exported HTML.

The adjacent **API base URL** selects the provider. The public default is
`https://api.openai.com/v1`. For the Azure v1 API, paste the deployment base
URL ending in `/openai/v1`; the application validates the official Azure host
and appends `/responses`. Enter the Azure deployment name in the **Model**
field. The endpoint identity participates in the cache key so results from two
providers cannot be mistaken for the same audit, but the API key never does.

For a trusted single-user machine, the launch environment remains an optional
fallback. Do not put the key in a source file, JSON configuration, or Git:

```powershell
$env:OPENAI_API_KEY = "your-key"
$env:GBI_OPENAI_BASE_URL = "https://your-resource.services.ai.azure.com/openai/v1"
python launch.py --foreground
```

Optional environment overrides are:

```powershell
$env:GBI_INTERPRETATION_MODEL = "gpt-5.5"
$env:GBI_INTERPRETATION_REASONING = "high"
```

Without an environment key, all deterministic traceback features remain
available and the GUI prompts for a session-only key when a whole-network audit
is requested.

## Reproducibility and limitations

- The database traceback is deterministic for a fixed project data snapshot.
- Literature search is time-dependent and model-dependent. The report records
  its generation time, model, reasoning level, citations, response identifiers,
  and per-batch usage metadata.
- The audit covers every unique node and edge in the displayed top-path union,
  not every hypothesis in the complete posterior graph. Changing the top-path
  display limit changes the research scope and cache key.
- Database membership does not prove operation in the requested cell type.
- A STRING association does not by itself prove binding.
- An OmniPath direction does not by itself establish collecting-duct activity.
- A human/rat BioGRID projection is not a direct mouse cell-type observation.
- An absence of retrieved literature is not proof of novelty.
- The standalone HTML archives the readable report, exact JSON, evidence
  ledgers, provenance records, citations, and all run artifacts.

Implementation entry points are `gui/evidence_provenance.py`,
`gui/literature_interpreter.py`, `gui/evidence_inspector.py`, and
`gui/session_report.py`.
