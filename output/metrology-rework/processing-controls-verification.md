# Processing controls verification

Verified on 2026-10-06 using a local, isolated copy of the 151-analysis SHP2L session. Screenshots use Portuguese labels.

## Comparison

- [Before, 1500 × 980](processing-controls-before.png)
- [After, 1500 × 980](processing-controls-after.png)
- [Narrow, 390 × 844](processing-controls-narrow.png)

The desktop panel content height decreased from 1450 px to 1138 px (21.5%). All 35 inputs and selectors remain available. The sidebar is 30 px wider to accommodate readable values. Labels measure 11 px, input values 12 px, and ordinary controls 30 px high. Long selected labels can wrap instead of truncating.

The comparison screenshots use the same original saved configuration. The narrow screenshot intentionally shows a long Z-axis parameter and the signal threshold used during interaction checks.

## Checks passed

- Desktop 1500 px, intermediate 760 px, and narrow 390 px: no page-level horizontal overflow. Range filters use two columns where space permits. Full timestamps and numeric values fit.
- The desktop panel has one scroll region and no nested scrolling descendants. Save stays pinned at the top while the panel scrolls. Narrow layouts use page scrolling with a sticky panel header.
- Date entry `2023-11-08 12:34:56.789` retains its fractional ordinal value, `738832.524268391`. Invalid `2023-02-30` reverts to the previous valid value.
- An out-of-range numeric lower bound clamps to the upper bound. Home and ArrowRight correctly move the lower slider handle; pointer dragging updates the input and plotted filter.
- Switching between date and numeric color parameters updates the gradient ticks and range fields. Long native select options remain available and fully readable.
- Filter changes update the plotted observations. Saving a signal threshold of 1.25 and a long Z-axis selection persists through reload; the Save button returns to disabled. Restore saved values discards an unsaved color-parameter change and clears the pending state.
- Original configuration restored in the isolated copy after verification.
- `npx tsc --noEmit`: passed.
- `node --test tests/scientific-range.test.cjs tests/i18n.test.cjs`: 7 tests passed.
- Browser reported no uncaught errors.

Shared panel, grouping, native selector, and compact range styles preserve the existing processing callbacks, filtering, available options, and save-state conditions. No analytical calculations were changed for this panel refactor.
