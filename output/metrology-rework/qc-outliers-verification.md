# QC outlier persistence and display

Implemented in Results Station and the shared qualification calibration view.

- QC sequences use all available final QC results. Before values remain limited to valid paired comparisons. Statistical outliers have an explicit marker trace and legend entry.
- Sigma/IQR detections are stored in append-only snapshots with evaluation IDs, population, method, threshold, limits and creation time. Settings are saved with reviewer attribution. Changed evaluation populations receive new snapshots.
- Long-term independent QC excludes saved statistical flags per isotope, in addition to the existing frozen-target 3 SD exclusion. Observations remain available and plotted. Frozen evaluation results, reviewed precision periods and method uncertainty components are not rewritten.
- Calibration controls show/hide statistical flags, validity-range flags, manual exclusions and failed analyses, plus independent isotope switches. Display settings are retained for this browser session and do not change statistics. Review categories come from the recorded evaluation; a pending acquisition is not relabeled as a failed acquisition.
- The sidebar chips have been replaced by category tables below the charts. Statistical rows show final value, expanded uncertainty, detection interval and population size. Each row opens the original analysis evidence and cycles.
- Consultation 3D/crossplot data now retain statistical outliers. The cleaned data used for fits and precision calculations remain unchanged.

## Checks

- 69 Python unittest tests passed.
- 20 frontend tests passed; TypeScript passed; git diff --check passed.
- Browser verification used a separate copy of the 151-analysis test session. IQR 1.5 persisted two oxygen flags. Reload preserved settings; history reported both saved flags and retained 29 of 37 oxygen observations after combining the existing and new exclusion rules.
- Disabling statistical flags kept 15 carbon results, changed oxygen from 15 to 13 plotted results, and changed the 3D/crossplot source from 37 to 35 visible points. Both flagged table rows remained available. Zero new requests; graph DOM nodes retained.
- The analysis review opened its cycle plot and measured-value/validity table.
- Desktop 1500 px and narrow 760 px checked. At 760 px, the document width was 745 px; the wide data table scrolled inside its container. No browser application errors.

## Screenshots

- [QC sequence and controls](qc-outliers-sequence.png)
- [Charts and outlier tables](qc-outliers-table.png)
- [Narrow layout](qc-outliers-narrow.png)
