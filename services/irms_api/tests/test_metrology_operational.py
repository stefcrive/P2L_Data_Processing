from __future__ import annotations

import copy
import json
import sqlite3
import unittest
from pathlib import Path

import pandas as pd

from services.irms_api.metrology.correction_review import comparison_rows, correction_review
from services.irms_api.metrology.importer import parse_legacy_export
from services.irms_api.metrology.models import MethodConfig, Decision
from services.irms_api.metrology.science import anchor_model
from services.irms_api.tests import test_metrology_sessions as fixtures


class CorrectionVerificationTests(unittest.TestCase):
    def fixture(self, wrong=False, n=15):
        config=MethodConfig()
        model=anchor_model([-5.,2.],[-2.,1.5],[.02,.02,.005,.005])  # b=2, not identity
        model['correction']=dict(slope=.03,u_slope=.001,domain=dict(low=2.,high=10.),center=6.,
            training_evidence='Independent training A',validation_evidence='Separate validation B',
            independence_rationale='Neither shares QC observations',evidence_asset_ids=['training','validation'])
        rows=[]
        for i in range(n):
            p=2+8*(i%3)/2
            raw=.03*(p-6)+[-.005,0,.005][i%5%3]
            # Verification does not fit coefficients. This is the supplied fixed model.
            result=model['intercept']+model['slope']*(raw+(.03 if wrong else -.03)*(p-6))
            rows.append(dict(id=str(i),sequence=i,role='qc',excluded=False,d13c=raw,d18o=raw,i44_v=p,
                             isotopes={iso:dict(value=result) for iso in ('d13c','d18o')}))
        models={iso:copy.deepcopy(model) for iso in ('d13c','d18o')}
        qc=dict(isotopes={iso:dict(passed=True) for iso in models})
        return rows,models,config,{},qc

    def test_verification_uses_same_scale_and_fixed_coefficients(self):
        args=self.fixture(); review=correction_review(*args)['d13c']
        self.assertEqual(review['status'],'eligible_for_review')
        self.assertGreater(review['sd_reduction_fraction'],.9)
        self.assertGreater(review['reduction_interval95'][0],0)
        raw_sd=pd.Series([r['d13c'] for r in args[0]]).std()
        self.assertAlmostEqual(review['before']['sd'],raw_sd*2)
        self.assertFalse(review['automatic_approval'])

    def test_wrong_correction_or_failed_bias_cannot_validate(self):
        args=self.fixture(wrong=True)
        self.assertEqual(correction_review(*args)['d13c']['status'],'not_improved')
        args=self.fixture();args[4]['isotopes']['d13c']['passed']=False
        self.assertEqual(correction_review(*args)['d13c']['status'],'review_required')

    def test_preapplied_correction_and_missing_qc_are_not_claimed_as_improvement(self):
        args=list(self.fixture());args[3]={'input_basis':'already_vpdb','preapplied_corrections':['d13c']}
        review=correction_review(*args)['d13c']
        self.assertEqual(review['status'],'already_applied')
        self.assertIsNone(review['sd_reduction_fraction'])
        args=list(self.fixture(n=3))
        self.assertEqual(correction_review(*args)['d13c']['status'],'review_required')
        self.assertEqual(comparison_rows(args[0],args[1],{'input_basis':'already_vpdb'})[0]['d13c'],args[0][0]['d13c'])

    def test_mock_transfer_cannot_validate_real_historical_results(self):
        args=list(self.fixture());args[3]={'calibration_verification':'simulation_assumption'}
        review=correction_review(*args)['d13c']
        self.assertEqual(review['status'],'review_required')
        self.assertTrue(any('historical' in r for r in review['reasons']))


class LegacyRawImportTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame([{'Row':2,'Method':'Normal sample size (2300mV - 17800mV)','Date':'11/18/23','Time':'00:28:37',
            'Identifier 1':'SHP2L','Identifier 2':'','d 13C/12C  Mean':-.7,'d 13C/12C  Std Dev':.02,
            'd 18O/16O  Mean':-5.8,'d 18O/16O  Std Dev':.03,'1  Cycle Int  Samp  44':7500.,
            '1  Cycle Int  Ref  44':8400.,'Information':'Total CO2 : 689;'}])

    def test_legacy_raw_units_dates_and_missing_metadata_remain_explicit(self):
        data=parse_legacy_export(self.frame(),'raw',['raw']);row=data['measurements'][0]
        self.assertEqual(row['acquired_at'],'2023-11-18T00:28:37')
        self.assertEqual(row['i44_v'],7.5)
        self.assertAlmostEqual(row['sample_reference_difference_v'],-.9)
        self.assertIsNone(row['pressure_mismatch_v'])
        self.assertIsNone(row['mass_ug'])
        self.assertEqual(row['raw_rows'][0]['values']['1  Cycle Int  Samp  44'],7500.)

    def test_processed_columns_are_rejected(self):
        frame=self.frame();frame['Double anchor interpolated corrected d13C']=-.8
        with self.assertRaisesRegex(ValueError,'Processed workbook'):
            parse_legacy_export(frame,'Input',['Input','Client Output'])


class SourceArchiveTests(unittest.TestCase):
    def test_source_archive_roundtrip_is_append_only(self):
        fixture=fixtures.ResultsSessionTests('test_persisted_session_pins_provenance_without_double_normalization')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        session=fixture.create();run,content=fixture.import_batch(session)
        service=fixture.service;command=Decision(actor='Scientist',reason='Archive original raw workbook')
        source=service.archive_session_source(session['id'],'raw.xlsx',content,command,relative_path='raw.xlsx',disposition='Imported raw acquisition',run_id=run['id'])
        self.assertEqual(service.repo.read_blob(source['sha256']),content)
        self.assertIn(source['id'],[a['id'] for a in service.results_session_detail(session['id'])['sources']])
        response=fixture.client.get('/metrology/session-sources/'+source['id'])
        self.assertEqual(response.content,content)
        with self.assertRaises(sqlite3.IntegrityError), service.repo.connect(write=True) as db:
            db.execute('DELETE FROM session_sources WHERE id=?',(source['id'],))


if __name__=='__main__': unittest.main()
