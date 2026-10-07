const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const page = fs.readFileSync(path.join(__dirname, '../src/app/(dashboard)/processing/page.tsx'), 'utf8');
const functions = page.slice(page.indexOf('function configEquals('), page.indexOf('function asNumber('));
const reconcile = new Function(ts.transpileModule(functions, {}).outputText + '\nreturn reconcileProcessingConfigDraft;')();
const saved = {
  overlays: {
    show_saturated_collectors: true,
    show_saturated_samples: true,
    show_failed_samples: true,
    show_manual_outliers: true,
  },
};

test('opening a saved session starts saturation and failure options off', () => {
  const draft = reconcile(null, saved, null);
  assert.deepEqual(draft.overlays, {
    show_saturated_collectors: false,
    show_saturated_samples: false,
    show_failed_samples: false,
    show_manual_outliers: true,
  });
  assert.equal(saved.overlays.show_saturated_collectors, true);
  assert.equal(reconcile(draft, saved, saved), draft);
});

test('user can enable an option and keep it across background refreshes', () => {
  const draft = reconcile(null, saved, null);
  draft.overlays.show_saturated_samples = true;
  assert.equal(reconcile(draft, saved, saved).overlays.show_saturated_samples, true);
});
