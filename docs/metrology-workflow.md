# IRMS Metrology Station

Run `start_metrology_app.bat` from the repository root. It opens `/metrology`, normally on port 3200, with the API on port 8200. Busy ports are skipped. The first page is Metrology overview; its dashboard precedes the Start here controls. Those controls appear only on the overview.

Optional launcher arguments: `-BackendPort 8200 -FrontendPort 3200 -Production -NoBrowser`. Install the normal project dependencies first with `setup.bat`. The metrology database, original files and reports live under `.data/metrology`; exploratory sessions launched by this script use `.data/metrology/exploratory-sessions`. Back up the entire directory together. Stop the application before copying a SQLite database, or use SQLite's online backup API.

## First qualification

1. Enter an operator/reviewer name and the justification for the next action. These are local audit attributions, not authenticated signatures.
2. In Reference materials, enter the actual NBS18 and NBS19 certificate values, uncertainty interpretation, VPDB scale, lot and certificate reference. Verify SHP2L's lot and uncertainty type. Supplied SHP2L values are retained as −0.75 ± 0.047‰ and −5.72 ± 0.089‰; no uncertainty type is assumed.
3. Open Qualification, the second main page, and create a qualification session. A periodic qualification can use a draft or copy the active method into a new draft. Its Session summary shows essential method conditions and QC statistics from that session only. Open Qualification settings to edit the method identity, acquisition conditions, acceptance criteria and uncertainty evidence. Confirm the acquisition timezone, initially `America/Sao_Paulo`. Mass and precision presets propose draft ranges and SD limits; they do not validate those ranges or change bias limits, calibration coefficients or evidence. Routine internal SD limits remain 0.04/0.05‰ and run-level SD limits 0.07/0.10‰ for carbon/oxygen; equality fails. Maximum QC spacing defaults to four unknowns. The configurable initial minimum is three independent QC aliquots per run.
4. The linked qualification loading plan defaults to NBS18, NBS19 and SHP2L at 60, 100 and 140 µg, five aliquots per combination. Matching uses ±0.5 µg. Import and inspect the carousel within Qualification; use Review & approval to record instrument checks, review effects and attach evidence. Creation records the starting method snapshot. Approval records the final method snapshot alongside the qualification and freezes the method. Older approved records expose their existing evaluation snapshot without rewriting the audit history.
5. Import the Qtegra carousel in qualification context and select that session. Inspect measured material groups, mass/intensity relationships, repeatability by mass, pressure residuals, drift and memory screening.
6. Review each residual effect, documenting evidence, magnitude and the response. A result with a nonzero fitted slope never activates a correction. Restricting a range requires changing the method range and re-evaluating. For an optional linear correction, attach independent training and validation evidence, configure the coefficient, standard uncertainty, predictor and domain in Qualification settings, and explicitly approve the relevant effect. The qualification carousel and routine QC must remain independent of coefficient estimation.
7. Evaluate after all method and run inputs are current. Review independent QC, normalization and uncertainty. Approval checks the current evaluation, instrument tests, carousel, material verification and effect reviews. Approval freezes and activates the method. The API also supports validation without immediate activation, activation and retirement. Later scientific changes require a new draft.

The mock workbook is explicitly synthetic. Its `__MOCK_INFO__` certificate examples are not imported into the material library. It supports descriptive diagnostics but cannot approve a laboratory qualification or release client results. Its seven sample cycles plus Pre are retained, and the reference-only final cycle does not enter sample-intensity means. The raw type `Standard` is recognized as a legacy/mock carbonate standard, while the six actual Qtegra choices remain distinct.

## Routine processing

Create a Results Station session with its client, project and method before importing
fresh data. Routine sessions default to values already normalized by Qtegra with the
selected qualified method. Record the actual transfer evidence and mark any residual
correction already applied. A workbook inherits this session provenance. The first
import fixes the session's client, method and input assumptions; another client or
method requires another session. Imports automatically assign each sample group from
Identifier 1 and Species, so matching pairs share a group across workbooks. Missing
species use Identifier 1 alone, with the original sample label as its fallback.
Groups can still be edited before release. Previously imported workbooks are migrated into consultation
sessions without altering their scientific records. Real client names are left
unassigned when the original data did not record them.

### Automatic residual correction

After the registered method, session QC is fitted against first-cycle sample
intensity, sample-reference difference and pressure-adjusted difference. For each
isotope, the candidate producing the greatest decrease in paired QC SD is applied
once. If no candidate decreases SD, the results retain the registered-method values.
At least the method's minimum QC count, three observations, a varying predictor and
positive residual degrees of freedom are required. These are alternative predictors;
the three corrections are never stacked.

Application includes samples, QC and anchors across the whole session, including
outside the method and fitted ranges. Manual exclusions, failed acquisitions and
missing predictors or required budget inputs remain ineligible for this ordinary
signal correction. Pressure-affected analyses can receive the separate pressure correction below. Out-of-range results
retain their review flags. A registered correction rejected only by its predictor
domain is reconstructed with its frozen coefficients and marked as extrapolated;
actual missing inputs are reported in the uncertainty panel instead of inventing a
budget. Acquisition failures retain their failure status even when a baseline budget
can be calculated.

The residual table is green for an applied correction, amber when QC SD does not
improve, and neutral for disabled, insufficient or unselected fits and diagnostic-only
effects. The gear beside each of the three signal predictors opens its isotope
parameters. Blank coefficients use the QC fit; manual coefficients require their
standard uncertainties. Saving reruns the same SD decision. There is no separate
preview or activation step. Chart filters do not change the correction population.
Saved statistical QC outliers are excluded from fitting unless enabled in the gear.
Screening uses the complete method-stage QC population, including values reconstructed
beyond the method predictor domain, and excludes acquisition failures. It runs before
residual fitting. Older incomplete screening caches are replaced by a new immutable
screening record. Showing failed acquisitions only changes visibility.
Outlier review distinguishes **Amostras com ajuste de pressão ruim** from
**Amostras sem sinal** (missing signal/delta or a recorded failed acquisition).
Neither category contributes to sigma/IQR estimates. No-signal analyses are
flagged as outliers. The **Considerar amostras com ajuste de pressão ruim como
outliers** toggle under **Detecção de outliers** defaults to off and persists with
the screening method and threshold. Enabling it explicitly flags pressure failures
as outliers, including unknowns, and prevents their admission to the corrected QC
pool. Turning it off restores eligibility for the existing SD-improvement decision.
Independent range limits and manual exclusions still apply. Charts and review
tables show distinct category labels and symbols; JSON exports retain the screening
settings and flags.
The separate **Corrige análises com falha** checkbox, below the all-data linearity
option, saves a session setting that attempts correction only on pressure-affected analyses.
An analysis is pressure-affected when Qtegra flags a pressure-adjustment failure.
The pressure limits set in Qualification produce separate range warnings. These
warnings do not trigger pressure correction or exclude QC from session residual fitting.
Missing pressure metadata alone does not establish a pressure issue. Failed acquisitions
and no-signal analyses remain ineligible for either session correction.
For each isotope, pressure-failed QC estimates a joint model of method-stage delta
against pressure-adjustment result minus target and initial sample intensity.
Manual and saved statistical exclusions do not train the model. Huber weights
initialize the fit, then iterative three-MAD residual screening precedes final OLS.
Screened QC IDs remain recorded and their observations remain visible. At least four
QC, the method minimum, full predictor rank and residual degrees of freedom are required.
The correction is `y_final = y - b*pressure - c*(initial_intensity-I0)`, with zero
pressure difference and the median initial intensity of retained nonfailed QC as
references. Unknown sample deltas never estimate these coefficients. Both predictors
are part of one correction with their complete coefficient covariance. A reduction
in fitting QC SD is required. If a joint model cannot be estimated, the pressure-only
model on nonfailed QC is explicitly labeled as a fallback. It requires at least three
QC, the method minimum, predictor variation and a reduction in fitting QC SD.
Pressure-affected analyses never receive ordinary residual linearity corrections,
including when pressure correction is disabled, lacks a usable model or lacks inputs.
Unknowns without pressure issues remain eligible for the ordinary selected signal correction.
The coefficient covariance contributes `g Cov(beta) gT` to the result budget. Extrapolation,
fit evidence and unsuccessful attempts are recorded. For each isotope, corrected
pressure-failed QC is added to the current nonfailed session QC pool only when the
candidate pooled SD is strictly lower. The candidates are evaluated together, and
the before/after statistics and admitted measurement IDs are recorded. Manual and
statistical exclusions and other acquisition failures remain excluded. Admitted QC
participates in final session QC statistics and diagnostic fits; original failure
flags remain visible. Ordinary model training and imported QC statistics retain their
original nonfailed population. The pressure model separately reports its retained
training population, fit exclusions and remaining intensity slope. A flat trend on
fitting QC is an in-sample result, not independent validation. Disabling the checkbox
restores the ordinary session calculation.
Charts, uncertainty budgets and new exports use the same saved setting.
Residual charts label applied and uncorrected observations separately; legend shapes
identify statistical outliers, range flags and acquisition failures. Open gray symbols
show the pre-correction values of observations that received a correction.

Result values, diagnostics, uncertainty budgets and new session exports use the same
calculation. For the coefficient contrast, `u_residual² = g Cov(beta) gᵀ`, with
`g = [x − x_ref]` for a line and a second term `x² − x_ref²` for a quadratic.
The existing budget covariance is retained and this component is added in quadrature.
Predictor, reference and offset are treated as fixed, and coefficient estimation uses
ordinary least squares residual variance. Manual coefficient uncertainties are treated
as uncorrelated. The QC SD reduction is an in-sample selection criterion, not independent
validation. See the [NIST regression calibration guidance](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/calibrat.htm).

Every calculation starts from the immutable run evaluation, so refreshing never adds
a correction twice. The calculation dossier records settings, the QC SD comparison,
coefficient covariance and each result's adjustment and uncertainty. Previously
exported artifacts remain unchanged.

Identifier 1, Identifier 2 and species can be corrected in Processing controls. These
edits are audited session overlays; original acquisition labels and material matching
are preserved. Labels persist across bridge recreation and appear in result records
and exports. Numerical edits still use Results Station's scientific review workflow.

Duplicate detection uses the original file SHA-256 within each session, including
renamed files. Importing the same bytes in another session creates independent run
and measurement records while sharing the immutable source-file archive.

The summary displays the applied qualification's frozen two-anchor model, QC SD and
bias for this run, and history for the same method population. Larger markers identify
the current session's QC. Method definition belongs to the session: draft parameters
are editable and approved parameters remain fixed. Calibration & linearity shows
the session's QC sequence, correction decisions and carbon/oxygen residual
diagnostics together, including intensity and pressure-adjustment linearity. The
original IRMS sigma/IQR algorithms flag session QC outliers by material; flags alone
do not exclude observations. The original Selection Editor and hover diagnostics,
3D calibration and crossplot remain available. Consultation imports use original
raw workbooks, map analyses by acquisition identity, and preserve exported means
separately from cycle-derived summaries. They never normalize a result again.

Processing charts cover every workbook in the session, with imported observations,
final values and expanded uncertainty. QC uses a secondary axis with the same scale
spacing. Incertezas is a separate tab with paired isotope budgets, formulas and
parameter help. Missing calculations remain unavailable, rather than becoming zero.
Click a sample in either uncertainty chart to select its carbon and oxygen budgets.
The result selector supports the same selection with the keyboard. Shaded intervals
show each result's expanded uncertainty and stop at missing budgets and workbook
boundaries. Incertezas initially shows final results; Show imported observations
reveals raw observations. Processing charts initially show both. Reused IRMS plots
retain their imported traces and add canonical final results, with uncertainty bands
in isotope series and axis uncertainty bars in crossplots and 3D views. Double-anchor
bands show pointwise `k*u_norm` from the full stored anchor covariance, not total
sample uncertainty or a simultaneous confidence band.

Development launchers reload the Python API when service code changes. When updating
an already running older instance, restart its launcher so its API matches the UI.
The plotting proxy allows up to five minutes to prepare a large raw-workbook session;
its saved consultation is reused on later openings. Retry plotting workspace retries
a failed load without changing the imported observations or scientific evaluations.

Import highlights analyses requiring review. Reviewers can annotate verified masses,
exclude an analysis or accept eligible sample-level deviations with a justification.
Accepting a deviation appends an audited evaluation and retains the original issues;
a new scientific evaluation requires another exception review. Acquisition failures,
missing uncertainty budgets, QC failures and qualification requirements remain gates.
Release actions are also in Import.

The sample-group filter controls result plots and CSV/JSON/XLSX/PDF/ZIP exports.
The separate Traceable results export tab reuses the original IRMS filename
convention and client worksheet formatter, with selectable sample/identifier sources.
Exports retain
the source hash, run/evaluation/method/qualification identifiers, per-result decision
and `u_prec`, `u_norm`, `u_corr`, combined uncertainty, coverage factor and expanded
uncertainty. ZIP combines CSV, Excel, the calculation JSON and a formatted PDF dossier
using the existing laboratory certificate layout. Review and blocked results remain
provisional in every format, and simulated qualifications retain their watermark.
Stored exports are
immutable and can be downloaded again. Releasing a run also freezes its client/session
and sample-group snapshot. Client separation is organizational within this local
workspace, not an authenticated multi-user permission system.

Import the original workbook in routine context. The active method is selected automatically. If none exists, the source and descriptive diagnostics remain available with no normalized approval. Explicit `MASS=<value> ug` or `mg` comments are parsed; bare numeric comments are never treated as mass. Missing masses can be supplied as separately audited weighed-mass annotations.

Qtegra `Running` statuses require an explicit completed-acquisition confirmation. Failed acquisition, pressure-adjustment failure, missing internal SD, insufficient QC, QC precision/bias failure, cadence violations, missing/out-of-range mass or signal, isotope extrapolation, overdue qualification and unresolved interventions block release. The imported `Evaluate` flag is retained as source metadata and does not flag a failure or exclude an observation. A historical acquisition predating method approval remains review-only. Exclusions require a reason and evidence; original points remain stored. Released runs cannot be edited or reprocessed.

Release uses complete acquisition timestamps, interpreted in the configured timezone. Qualification must have been valid at acquisition as well as at release. A later intervention verification cannot retroactively qualify an earlier routine acquisition. Historical imports are assigned to the intervention period that covered their acquisition time. Missing or ambiguous timestamps block release.

Every evaluation is stored. QC charts select each run's current evaluation to avoid counting reprocessing as new analytical evidence. Historical control signals block release within their instrument period. Review a homogeneous period across multiple acquisition dates before using its individual-observation SD as intermediate precision. Do not substitute SE of a mean.

## Scientific models

The normalization is `y = A1 + (x - M1) * (A2 - A1) / (M2 - M1)`. Its Jacobian with respect to `[A1, A2, M1, M2]` is `[1-t, t, b*(t-1), -b*t]`, where `t=(x-M1)/(M2-M1)`. Assigned standard uncertainties and measured anchor-mean SEs populate a covariance matrix. A laboratory-supplied covariance matrix must be symmetric, positive semidefinite and have matching diagonal variances. Anchor separation must exceed six times its standard uncertainty to avoid a singular ratio model. Educational mode offers reproducible normal-distribution Monte Carlo propagation for comparison.

The final budget combines documented precision and sample-dependent normalization uncertainty, plus explicit independent components. Normalization includes assigned-value uncertainty. Covered-source metadata rejects duplicate RM terms and components already covered by precision. Cross-component dependence is supported by the scientific covariance function; the current method UI accepts independent final-budget components only and requires their independence rationale. It does not silently assume a supplied dependent model is independent.

The optional secondary model is `z = x - c(p-p0)`, applied to anchors, QC and unknowns before normalization. Supported predictors are weighed mass, sample I44, pressure mismatch and sequence position. One predictor per isotope avoids pretending correlated effects are independent. Its coefficient must come from an independent training study, with separate validation evidence and documented negligible predictor uncertainty. Coefficients, supporting files and their reviews are frozen with the method. The normalized sensitivity to the shared coefficient is `dy/dc = b*((1-t)*(P1-p0) + t*(P2-p0) - (p-p0))`, where P1/P2 are the anchor predictor means. This captures cancellation when anchors and sample share the predictor. Coefficient uncertainty is included once in the final budget. Joint Monte Carlo varies that coefficient consistently across anchors and sample. Anchor-mean covariance is conditional on the coefficient; the precision evidence must not already include its estimation uncertainty.

OLS diagnostics expose slope/intercept, parameter covariance, residual standard error, classical and HC3 covariance, confidence intervals and effect span. These are exploratory associations. Mass-varying QC can confound univariate drift or memory estimates; use the mass-specific repeatability and experimental design. Memory screening needs at least six paired QC/predecessor observations and never establishes causality or activates a memory correction.

The implementation records Qtegra's instrumental linearity information but does not reconstruct the proprietary correction. `Ref Gas Calibration` characterizes the reference gas, not application carbonate anchoring. `Delta Standard (DualInlet)` is instrument evidence. `Drift Correction` rows violate this laboratory's policy and block approval. Only configured carbonate material labels supply anchors; QC is independent.

## Current scope and remaining extensions

Implemented: local persistence, immutable imports, material revisions, method freezing, qualification and evidence, analytical diagnostics, covariance/Monte Carlo anchoring, uncertainty safeguards, individual QC history, reviewed precision periods, exclusions, interventions, release gates, three PDF report types with archived JSON, and calculation inspection.

The supported applied correction is a centered linear model selected and reviewed by the scientist. Memory corrections, jointly fitted multiple effects, nonlinear equations, non-negligible predictor uncertainty and correlations between coefficient estimates and anchor inputs require an expanded measurement model. This version supports their investigation and range restriction but cannot apply them. Automatic change-point estimation, authenticated electronic signatures and multi-user deployment are outside this local release.

## Reports and audit

Qualification dossiers include method conditions, material lots, carousel, test/effect reviews, QC, uncertainty and diagnostics. Client PDFs require a released result and contain final values, expanded uncertainties, k, VPDB, method and reviewer. Historical PDFs include separate QC populations and charts. Each PDF and JSON calculation snapshot is content-addressed and downloaded from stored bytes. Source and asset downloads verify their SHA-256 hashes. SQLite records and hash-linked audit entries resist accidental application-level changes; this is not an external tamper-proof archive against a database administrator.

## Validation

Run `python -m unittest services.irms_api.tests.test_metrology -v` using the project environment. Run the broader backend suite with `python -m unittest discover -s services/irms_api/tests`. The repository baseline has two pre-existing diagnostic-grid ordering test failures; their names and baseline reproduction are recorded in [the implementation validation notes](metrology-validation.md).

The supplied workbooks are local user data, not committed fixtures. To import them explicitly, use `python -m services.irms_api.metrology.cli --routine <path> --carousel <path> --actor <name> --reason <justification>`. This does not approve anything or copy mock assigned values into the library.
