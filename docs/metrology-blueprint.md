# Metrological application gap analysis and implementation blueprint

Date: 2026-10-05. Branch: `feature/irms-metrological-platform`.

## Scope and precedence

The user's request authorizes planning and implementation. The attached master prompt's "do not code yet" is reference-document content, not a restriction on that request. The user's Qtegra rules take precedence: gas-reference calibration determines the reference-gas composition, Qtegra drift correction is not used, and repeated independent QCs support drift, memory and bias investigations.

## Current architecture

FastAPI serves a Next.js 15/React 19 dashboard. Python/pandas modules separate workbook import, cycle diagnostics, calibration, processing and export. `FileSessionStore` stores session metadata, CSV snapshots, state and JSONL events. A bounded in-process job queue handles long operations. The reference-material table stores official values but does not constitute a certified-lot library. The existing UI has import, diagnostics, calibration, processing and assistant workspaces.

Retain the existing workflows, shared workbook parser, diagnostic tools, Plotly components, dependencies and test suite. Do not silently upgrade exploratory sessions into approved metrological results. Existing uncommitted user edits remain intact.

## Gap map

| Current capability | Required capability / gap | Scientific risk | Software and data change | Verification | Priority |
| --- | --- | --- | --- | --- | --- |
| Workbook parsing and session files | Permanent original import and parser provenance | Results detached from input | SHA-256 content-addressed files, raw imports, immutable measurement and cycle records | Byte identity, duplicate import, restart, malformed input | P0 |
| Label-based standard selection | Exact Qtegra types plus configurable material aliases | Gas calibration or QC becomes carbonate anchor | Preserve source type; distinct conditioning, DI standard, disabled drift, QC, gas calibration and unknown roles | All six types, unrecognized type, conflicting aliases | P0 |
| Editable calibration state | Frozen method versions and certified lots | Historical result changes with settings | SQLite methods, material-lot snapshots, ranges, conditions, criteria, approval decisions | Invalid transitions, no update after approval, historical replay | P0 |
| General diagnostic plots | Periodic/event qualification and evidence | Unqualified system appears valid | Qualification sessions, configurable tests and carousel, attachments, interventions | Missing/failed evidence blocks approval; targeted tests | P1 |
| Linear fits and isotope calibration | Explicit uncertainty of anchoring | Exact two-point line falsely implies zero uncertainty | Jacobian/covariance propagation and seeded Monte Carlo for two uncertain anchors | Hand calculations, MC agreement, singular anchors, extrapolation | P1 |
| Outlier controls | Documented exclusions | Selective precision improvement | Append-only exclusions and reasons; original observations remain | Exclusion provenance and retained raw points | P1 |
| Session statistics | Individual historical QC and homogeneous periods | Mixing states understates uncertainty | Run-linked QC observations segmented by method, lot, configuration and intervention period | Individual SD versus SEM, shifts, trends, period isolation | P1 |
| Optional exploratory corrections | Explicit effect reviews and approved measurement equations | Automatic correction or double correction | Effect evidence, approval/rejection, actual equation, domain and covariance | No automatic correction, insufficient evidence, dependency checks | P1/P2 |
| Exportable processed numbers | Reviewed release with method/QC gates | Valid-looking failed results | Frozen evaluation, release snapshot, reviewer, reasons, report hash | Missing QC, failed QC, ranges, expired qualification, intervention | P1 |
| Workbook exports | Internal dossier, client certificate, historical report | Reports omit provenance or misstate approval | Reproducible PDF/JSON generated from stored records | Content, page rendering, blocking unreleased certificate | P2 |
| Scientific assistant | Optional educational explanation | Formula only exists in UI | Server-side calculation evidence/formulas, inspectable audit | Backend numerical tests, browser workflow | P2 |

## Target architecture and migration

Add `services/irms_api/metrology` as an independent domain with a transactional SQLite repository, strict Pydantic commands, deterministic scientific functions and an API router. Add `/metrology` to the web application with its own navigation. A dedicated launcher uses separate ports, Next.js build output and `.data/metrology` storage. The existing `start_app.bat` continues to launch the results station.

Relational tables hold material lots, methods, qualifications, evidence, interventions, raw imports, runs, measurements, evaluations, QC observations, exclusions, reviews, releases, reports and audit events. Versioned JSON documents inside rows retain complete method/uncertainty models without premature schema proliferation. SQLite foreign keys and transactions protect scientific transitions. Content-addressed files are verified when downloaded. The local workflow records declared operator/reviewer names; it does not pretend these are authenticated electronic signatures.

No automatic migration or activation of legacy calibration. Import original exports into the new workspace, register certificate values and lots, define the method, qualify it, then explicitly approve and activate it. Synthetic fixtures are tests only, never laboratory reference values.

## Workbook observations

The supplied Petrobras workbook has a `New Table` sheet with four header rows and per-analysis `Pre` plus numbered cycle rows, and separate intensity sheets. Preserve the entire workbook. Parse acquisition fields such as sample type, reference, sample/reference I44, pressure adjustment, source pressure, acid temperature and Qtegra linearity result. Use exported elemental delta means, not raw delta or molecule delta columns. Cycle repeatability is distinct from repeatability across aliquots.

The export does not supply an explicit sample-mass column. Missing mass is reported as missing, never inferred from signal or a sample comment. A separate, audited mass annotation can supply weighed masses. Full carbonate validation requires a designed multi-material/multi-mass experiment, not simply any routine workbook.

## Implementation sequence

1. Foundation: separate launcher/workspace, database, immutable import, types, material lots, method drafts and audit. This creates the most architectural value without rewriting processing.
2. Qualification: structured tests and files, carousel, matching of materials, descriptive diagnostics, decisions on effects.
3. Scientific engine: two-point normalization with covariance and Monte Carlo, per-result uncertainty, uncertainty-dependency checks.
4. Operational workflow: approval gates, active method, routine imports, independent QC, historical periods, reviewed release, interventions.
5. Reports and education: PDF certificate/dossier/history and calculation evidence.

Advanced correction equations must be explicitly selected and supported by independent validation. No proprietary Qtegra linearity reconstruction. No automatic drift or memory correction. Missing experimental design is an unevaluated hypothesis, never a negligible effect.

The implemented secondary model is one centered linear predictor per isotope, with independent coefficient evidence, a validated domain and explicit scientist approval. Uncertainty propagation includes the shared coefficient's influence on both anchors and the sample. More general dependent or nonlinear models remain extensions to this measurement model.

## Scientific validation and test strategy

Use synthetic, analytically known datasets to test OLS slope/residual covariance, homogeneous-material grouping, confounded mass/intensity/pressure predictors, drift and contrasting-predecessor memory evidence. Check two-point propagation with hand derivatives and independent seeded Monte Carlo. Confirm assigned uncertainty conversion, PSD covariance checks, extrapolation and near-coincident anchors. Budgets reject overlapping source coverage unless dependence is explicitly resolved.

Exercise the complete lifecycle through the API, including approval, release, restart and report reproduction. Test negative cases as heavily as the happy path. Import the user's workbook without editing it and reconcile analysis/cycle/type counts. Run the existing backend regression suite, TypeScript checks, a production build and a browser walkthrough of the new operator workflow.

## Scientific references

- [JCGM 100:2008](https://www.bipm.org/en/doi/10.59161/jcgm100-2008e), measurement models and covariance in uncertainty propagation.
- [JCGM 101:2008](https://www.bipm.org/en/doi/10.59161/jcgm101-2008), propagation of distributions using Monte Carlo.
- [IAEA reference materials](https://analytical-reference-materials.iaea.org/), authoritative source of current material certificates. Values must be entered from the actual lot certificate.

This implementation does not establish accreditation or validate a laboratory method by itself.
