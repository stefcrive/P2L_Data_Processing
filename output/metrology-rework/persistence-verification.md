# Results Station persistence and review verification

Implemented on 2026-10-06 in the existing application. Verification used isolated copies of the operational and demonstration databases.

## Behavior

- The metrology layout retains the open Results Station across section navigation. The last session and tab can be reopened; visited processing, calibration and diagnostic workspaces remain mounted with separate session IDs.
- The processing results table and figures retain their DOM instances. Unchanged queries stay cached; scientific run/evaluation/method revisions invalidate the linked workspace. Species detail charts load when expanded.
- Identifier 2 previews sort delta values, uncertainty arrays, markers and sample identities together, including binary Plotly vectors. No observations are removed by this ordering change.
- Legend controls remain usable after global legend collapse. Calibration cards and plotting backgrounds are white. Both calibration views add before/after values on the original isotope axes and preserve the selected Z parameter.
- Sample intensity versus delta and sample-minus-reference intensity versus delta have distinct diagnostics and manual preview controls. Pressure-adjustment error remains separately named; existing pressure coefficients are not reinterpreted.
- Analysis review displays identity, acquisition state, measured values, validity limits, original cycles and archived analysis cells. Missing observations remain missing.
- Carbonate selection is inside workbook import. The existing alpha factors (calcite 1.0087, aragonite 1.0091) are explicit. Unknown-sample oxygen results and their uncertainties receive the conversion once; certified QC/anchor bases remain unchanged. The already-applied option retains corrected exports. Existing immutable evaluations are not rewritten.

## Verification

- TypeScript: passed.
- Python: 66 tests passed, including import persistence, full uncertainty scaling, carbonate duplicate protection, cycle evidence, distinct linearity predictors and cached workspace upgrades.
- Frontend: 16 tests passed, including binary vector ordering, uncertainty envelopes, isotope-axis preservation, color-scale integrity and date range input handling.
- Browser: 1,056 analyses across 34 workbooks. Tab navigation retained the same Plotly and results-table elements with zero analysis/workspace requests. Ten isotope lines had zero backwards Identifier 2 segments.
- Calibration navigation: 15 retained figures, zero `plotly_afterplot` events and zero API requests on summary/calibration round trips.
- Existing parsed workspace upgrade: 1,056 mapped analyses, same workspace ID, no Excel parsing; 11.682 seconds in this local verification. A completely new 34-workbook bridge still requires initial parsing (roughly a minute in this development environment); subsequent navigation reuses it.
- DGL-2024 analysis 4: I44 8.6440 V versus 3.5500–8.4500 V, pressure-adjustment difference −0.5390 V versus −0.0200–0.0200 V, eight archived cycles, and the missing final sample cycle retained.
- Desktop 1500 px and narrow 760 px: no page-level horizontal overflow. Legends shown/hidden, resizing by keyboard, zoom, pan and trace toggling verified. PNG export produced a valid 13,495-byte image/png with the PNG signature. Browser reported no application errors after final changes.

## Screenshots

Prior layout: [calibration before this update](density-calibration-after.png).

Updated [calibration comparison](persistence-calibration-comparison.png), [large processing session](persistence-large-session.png), [DGL analysis review](persistence-dgl-analysis.png), and [narrow analysis review](persistence-dgl-narrow.png).

Restart the running application to load the backend changes. The import default is calcite-referenced input; select the already-applied option for an aragonite-corrected export.
