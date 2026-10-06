# Operational laboratory simulation

Start `start_metrology_operational.bat`, or pass `-Operational` to `start_metrology_app.bat`.
The isolated database is `.data/metrology-operational/metrology.sqlite3`. Original workbook bytes,
downloadable exports and evidence are stored in its content-addressed `blobs` directory.
Keep the entire workspace together when backing it up. Relaunching preserves sessions and decisions.

The simulation contains five 45-analysis qualification carousels, with NBS18, NBS19 and SHP2L
at 60, 100 and 140 µg, five aliquots per material and mass. Four assessments are approved in
the simulation and the latest is ready for review. Thirty-nine mock routine runs provide
234 historical QC observations. Training and validation experiments are separate from the
qualification and routine observations. Assigned anchor values and certificate details are
illustrative demo records, not verified laboratory certificates.

## Imported raw series

| Session | Imported workbooks | Analyses | Unknown samples | Raw files archived |
|---|---:|---:|---:|---:|
| Venancio 383 U1539 | 1 | 151 | 114 | 1 |
| Igor Venancio MD28-3678 | 34 | 1,056 | 823 | 35 |
| Natan Pereira BTS coral | 19 | 817 | 600 | 37 |

The 484 identified QC observations include acquisition failures or missing isotope values.
Each isotope chart reports its number of available observations. Three additional observations
match anchor material identities. Original source labels and identifiers remain unchanged.

Only acquisition exports were imported. The Petrobras folder was omitted at the user's request.
Client Output reports, Processor.xlsx and saved processing snapshots were excluded.
Nineteen alternative or duplicate raw exports are archived without duplicating observations.
Selection favors main-folder completed exports over backup folders and temporary copies;
the archive records the decision for each file. No exclusions depend on whether a value passes QC.

The U1539 workbook is a legacy ISODAT raw row export. It retains original means and SDs,
converts first-cycle I44 from mV to V, and sorts by the explicit US-format acquisition date
and time. Its original rows remain stored. First-cycle signals are distinguished from Qtegra
cycle means. Sample/reference imbalance is not substituted for missing pressure-adjustment data.

## What the simulation demonstrates

The Results Station opens each client series with its raw workbooks, QC summary, paired
correction verification, linked two-anchor model, original IRMS plotting components and
uncertainty calculations. The raw archive downloads the exact original files with SHA-256
verification. CSV and JSON/ZIP exports preserve source identifiers, dates, original values,
processing decisions, model lineage and uncertainty components where calculable.

Real observations use `calibration_verification=simulation_assumption`. They retain their
externally referenced values without another normalization. The displayed model belongs to
the current mock qualification and does not prove which calibration produced a historical
export. Consequently these results cannot be released. Masses absent from source exports are
left missing. Corrections are calculated only inside their demonstrated predictor domain;
original observations remain visible when a final corrected value cannot be calculated.
Values beyond the anchor interval remain flagged as extrapolations.

Observed QC and mock QC are separate historical populations. Unverified historical client
series are also kept separate. Observed historical plots use original exported values,
including QC for which the mock correction cannot be calculated. Their dispersion is not
silently adopted as the current method's intermediate precision. The mock method retains
its reviewed, frozen `u_prec` from mock history.

## Correction verification

Before/after SD uses the same QC aliquots on the same VPDB scale with a fixed normalization
slope. An isotope transformation alone therefore cannot masquerade as improvement. The
configured coefficient is never fitted to these verification QC observations.

Default demonstration criteria are six paired QC aliquots, at least 5% SD reduction, a positive
lower bound in a 95% paired bootstrap interval, acceptable final QC, separate training and
validation evidence, and a resolved, practically relevant coefficient. Practical screening
thresholds are 0.01‰ for carbon and 0.02‰ for oxygen across the observed predictor range.
These configurable defaults are illustrative and need laboratory review.

Bootstrap intervals use 2,048 paired resamples and assume independent aliquots. Drift, memory,
collinearity and multiple exploratory effect screens require scientific review. Numerical
eligibility does not activate a method. Qualification approval checks this verification along
with the existing instrument, carousel, evidence and uncertainty gates.

The mock qualification reduces QC SD by approximately 60% for carbon and 65% for oxygen.
Real datasets also demonstrate unfavorable corrections: the recent Natan series 17.4 has
increased SD on its seven correctable QC pairs. The UI identifies this as no improvement;
it does not validate the mock correction for that historical run.

## Verification

Targeted tests cover raw legacy units/dates, rejection of processed columns, paired scale
comparison, harmful corrections, missing evidence, already-applied corrections, unverified
historical transfer, immutable raw archives and the complete metrology/session workflows.
Browser checks exercise the operational overview, client sessions, correction plots, reused
calibration components, source archives and export flow in Portuguese and English.

Local seeding command:

```powershell
.venv/Scripts/python.exe -m services.irms_api.metrology.operational
```

Optional `--source-root` and `--data-dir` arguments allow another explicit source root or a new
isolated workspace. An existing completed workspace is reused, not overwritten or reseeded.
