# Processing chart fixes

Verified on 2026-10-06 with isolated copies of the SHP2L test session and the operational BTS dataset, including its 19 workbooks and original cycles. The operational databases were not edited.

## Changes

- Cache transformed figures and preview masks until their scientific inputs or display configuration change. Hover and query updates reuse those figures.
- Mount processing figures near the viewport and retain them afterward. Remove the forced second Plotly render, keep its configuration stable, and explicitly attach current interaction handlers after initialization. This also repairs handlers lost during React development-mode initialization.
- Align the primary and secondary dataset means with equal isotope units per pixel. Preserve axis direction, original values, outliers, and uncertainty extents. Zoomed primary ranges continue to align the secondary axis.
- Put the legend checkbox inside Exibição, use an SVG help icon, and keep menu labels on readable rows.
- Keep each range filter across the full controls column.
- Count exclusions as a union of acquisition identities. Outlier tables contain each acquisition once, retain all failed criteria, and preserve failed-sample recovery actions.
- Enlarge the sample preview to 1240 px where space permits, with a 480 px chart panel and 10 px legend. Position it beside, above, or below the point; constrain its scroll area when the viewport cannot accommodate the full card. Clicking the preview opens the sample editor. Only its column header stays sticky.
- Use processing outlier symbols and colors in calibration. Before markers are larger and open; after markers are smaller and filled. Scalar legend markers match the stage symbols, and toggling a stage includes its fit line.
- Default diagnostics to initial sample intensity.

## Verification

- TypeScript passed after the final component edits.
- 83 backend tests passed, including overlapping exclusions, manual exclusions, and partial-saturation settings.
- 22 frontend tests passed, covering axis alignment, reversed axes, extreme values and uncertainty, unique table rows, calibration stages, outlier styles, and localization.
- The 151-analysis session retained its chart DOM through summary/processing navigation with zero chart redraws and zero new API requests.
- Operational BTS summary: 600 sample measurements, 63 unique excluded acquisitions, 537 final analyses. Its broader review tables contain 80 unique acquisitions across 166 category occurrences. The table scope also includes standards and review categories that need not exclude a sample.
- First operational processing load displayed two charts and deferred the isotope summaries until scrolling. Cold development-mode loading still took approximately 7.9 seconds, including a 4.0-second workspace request and a roughly 2-second Plotly startup task. This is not a claim of instantaneous cold loading; repeated figure rebuilding and forced initialization redraws were removed.
- Native hover opened the operational BTS cycle preview with all six intensity series and cycle limits. Its Plotly figure measured 432 px inside the 480 px panel; legend text measured 10 px. The hovered point remained outside the preview rectangle. Clicking a table cell opened the correct sample editor.
- At 760 px viewport width, all four range fields measured 699 px inside a 721 px panel, without page-level horizontal overflow.
- Legend hide/show, readable menu layout, diagnostics default, and distinct calibration markers were checked in the browser. Concurrent workspace edits caused some development-server reloads during verification; those edits were preserved.

## Screenshots

- [Processing charts](chart-rendering-processing.png)
- [Display menu](chart-display-menu.png)
- [Operational cycle preview](chart-sample-cycles.png)
- [Full-width filters on a narrow viewport](chart-controls-full-width.png)
- [Calibration stages](chart-calibration-stages.png)

Restart the running application to load the backend counting change.
