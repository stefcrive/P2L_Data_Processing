# Metrology demonstration workspace

Start **`start_metrology_demo.bat`**. It creates `.data/metrology-demo` on first launch and reuses it afterwards. `start_metrology_app.bat` continues to open the laboratory workspace. The launcher prints the actual address and selects another free port if necessary.

All demo observations, instrument checks, certificate metadata, approvals and releases are simulated. The demo has its own database and workbook store. The ordinary laboratory workspace still rejects synthetic data for method approval and client release. No laboratory reference values are replaced.

## What is populated

| Content | Demonstration |
|---|---|
| Period | 5 April–5 October 2026 |
| Approved qualifications | 4, at 60-day intervals; method v4 active |
| Recent qualification | 4 October; method v5 fully evaluated, awaiting approval |
| Each carousel | NBS18, NBS19 and SHP2L × 60/100/140 µg × 5 replicates = 45 analyses |
| Routine history | 39 runs, 20 unknowns and 6 QC aliquots per run |
| Historical QC observations | 234 individual SHP2L results, separated by method version |
| Reviewable precision periods | 4; SD of individual QC observations across days |
| Workbooks | 44 imported exports plus 2 independent correction studies |
| Reports | Historical performance, qualification dossier and a simulated client report |

The two correction studies use different materials and independent random draws. The fitted residual intensity coefficients are approximately 0.009‰/V for carbon and 0.015‰/V for oxygen. Their uncertainty is propagated through both the sample and the anchor means. No mass term is added on top of the correlated intensity term. Drift and memory are monitored, without applying corrections to them. The generator contains no drift or carryover term.

Routine QC uses independent seeded normal errors for carbon and oxygen, with standard deviations of 0.028‰ and 0.043‰. It does not alternate signs by aliquot or repeat a six-point pattern. The September failure adds deliberate biases of 0.19‰ and 0.28‰. The long-term acquisition-date chart shows individual points without connecting different runs.

The instrument acceptance values are illustrative examples with explicit units and criteria. Anchor assignments and uncertainties are synthetic certificate examples, not verified NBS documentation. SHP2L uses the supplied −0.75 ± 0.047‰ and −5.72 ± 0.089‰, interpreted as **standard uncertainty only in the demo**. The laboratory records retain their unverified uncertainty interpretation.

## Suggested evaluation sequence

1. Open **Six-month QC history**. Switch isotopes and inspect the September QC failure. Each method has its own frozen control limits; qualification mass-series observations do not enter routine history. A reviewed period is required before historical SD becomes a method's `u_prec`.
2. Open **Recent qualification**. Use **Residuals**, **Carousel**, **Normalization** and **Uncertainty**. Switch materials and isotopes. The original and final results are plotted together, with fitted slopes and confidence intervals. The normalization view shows the two anchors and sample-specific `u_norm`.
3. Open **Passing routine run**, then **processing**. Review the QC summary, values and uncertainties. **Review and release results** is enabled. Releasing is an audited change in the demo only. Generate its simulated certificate afterwards.
4. Open **Out-of-range sample**. The independent QC passes, but one unknown has a mass of 180 µg and is blocked. Open **Historical QC failure** to inspect a different reason for blocking.
5. Open **Qualification**. Instrument checks, effect reviews, evidence files and the current evaluation are already filled. **Approve and activate method** exercises the v5 activation gate. Do this after testing routine release: activating v5 supersedes v4, and unreleased v4 runs then require review.

The top upload area starts either workflow. Download the three example files there, or use `.data/metrology-demo/examples`. Re-uploading an unchanged workbook opens its existing import; it does not duplicate QC history. Select an open qualification session for a carousel, or the active method for a routine run. After a new import, evaluate it in **processing** to populate the Results Station and append its QC observations.

For already externally referenced exports, expand **Processing already performed in Qtegra** and record the matching frozen method and processing evidence. Mark any configured residual corrections already applied. The processor retains externally normalized values, applies only remaining stages, and reconstructs model inputs only for uncertainty propagation. Missing or mismatched provenance blocks release. Establishing a new qualification requires deltas before normalization.

## Example criteria

| Criterion | δ¹³C | δ¹⁸O |
|---|---:|---:|
| Individual internal SD | <0.04‰ | <0.05‰ |
| Run-level QC SD | <0.07‰ | <0.10‰ |
| Absolute run-mean QC bias, demo choice | ≤0.07‰ | ≤0.10‰ |
| QC spacing | At most 4 unknowns | At most 4 unknowns |
| Expanded uncertainty | k=2 | k=2 |

The validated mass range is 60–140 µg; intensity is 3.55–8.45 V and pressure mismatch is ±0.02 V. Isotope ranges, preparation, acquisition timezone, lots, certificate dates, precision evidence, correction evidence and coverage rationale are populated in the method and material editors. Unused optional uncertainty terms remain absent to avoid counting the same effect twice.

For each result, `u_c² = u_prec² + u_norm² + u_corr² + Σ u_j²`; `U = k u_c`. Anchor measured-mean uncertainties use their standard errors. Routine intermediate precision uses individual-observation SD. The full anchor covariance and the shared correction-coefficient sensitivity are available in the calculation record. Errors from changing operating conditions are blocked or reviewed, not hidden by inflating uncertainty.

## Reproducibility and checks

`scripts/generate_metrology_demo_inputs.py` supplies reproducible numerical matrices with seed 2532026. Packaged XLSX files were authored and visually inspected using `@oai/artifact-tool`; the application consumes these fixtures without needing that authoring dependency. `services/irms_api/metrology/demo_assets/manifest.json` describes their provenance.

`python -m services.irms_api.metrology.demo --data-dir <new-demo-directory>` creates another isolated copy. An existing workspace is not overwritten. The generator passes imports, evaluations, evidence reviews, method approvals and releases through the ordinary service checks; only the explicit demo mode permits simulated approvals and a simulated historical clock. Demo report titles and page footers identify their status.

Fixture updates apply to newly seeded workspaces. Existing demo and operational workspaces retain their imported observations, frozen precision baselines, approvals and audit history. Use a new data directory to evaluate the updated simulated QC. To refresh the packaged fixture values while retaining their workbook layouts, run `python scripts/generate_metrology_demo_inputs.py --package-assets services/irms_api/metrology/demo_assets`.

Verification: 37 metrology tests pass, including seeded approval/release, workspace isolation, historical QC separation, blocked range checks, exact uncertainty combination, and prevention of repeated normalization/correction. The wider backend suite currently has two existing failures related to diagnostics tab ordering. Browser checks cover plotted diagnostics, carousel, equations, populated qualification forms, passing/blocked routine review and report links. The production frontend build passes.
