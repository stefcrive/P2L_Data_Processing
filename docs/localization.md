# English and Portuguese

The shared header includes an EN/PT selector on all pages, including IRMS Metrology. English is the initial language. The choice is saved in `irms.language.v1` in browser storage, follows navigation and reloads, and synchronizes between tabs. If browser storage is unavailable, the selector still works for the current visit. Switching languages preserves component state and pending form edits.

`apps/web/src/components/layout/language-provider.tsx` supplies `useLanguage()` and `useTranslation()`. Wrap interface text and display labels with the translator returned by `useTranslation()`. Add Brazilian Portuguese text to `apps/web/src/lib/i18n/pt-BR.json`. English text is the lookup key. Dynamic captions use numbered placeholders such as `{0}`. Keep the same placeholders in both languages. Unrecognized text remains unchanged.

Translate presentation text only. Keep API paths, enum values, source column names, stored records, file names, raw calculation JSON, and numerical data unchanged. Scientific units and notation must retain their meaning. The chart formatter translates display fields while preserving data vectors, point identifiers, and source metadata. Plotly's Portuguese locale supplies toolbar captions. New assistant requests specify the selected response language; existing conversation content is preserved.

Metrology uses the shared site header, logo, fonts and CSS variables for its blue-and-slate palette, cards and controls. Its sidebar provides navigation within the metrology workflow.

Run `npm run test:i18n` in `apps/web` for translation, interpolation, notation and chart-data checks. Run `npx tsc --noEmit` and `npm run build` for integration checks. In the browser, check both languages, reload persistence, navigation, unsaved form values, and desktop/mobile layouts.
