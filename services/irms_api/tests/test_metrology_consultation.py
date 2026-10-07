import copy
import unittest
from services.irms_api.metrology.pipeline import process_value, diagnostics
from services.irms_api.metrology.science import anchor_model
from services.irms_api.metrology.analysis_evidence import analysis_evidence

class ConsultationTests(unittest.TestCase):
    def test_original_linearity_algorithm_honors_session_exclusions(self):
        import pandas as pd
        from services.irms_api.domain.calibration.workspace import build_calibration_workspace
        frame = pd.DataFrame({"Identifier 1": ["SHP2L"]*8, "Identifier 2": [""]*8,
                              "d 13C/12C  Mean": [float(i)*.1 for i in range(7)]+[30.],
                              "d 18O/16O  Mean": [float(i)*.2 for i in range(8)],
                              "1  Cycle Int  Samp  44": [float(i+1) for i in range(8)]})
        config = {"selected_standards": ["SHP2L"], "calibration_type": "Z-Score", "sigma_level": 99,
                  "fit_excluded_rows": {"d13C": ["7"]}}
        workspace = build_calibration_workspace("test", frame, {"metrology_link": {"results_session_id": "test"}}, config)
        self.assertAlmostEqual(workspace.linearity_fits["d13C"]["slope"], .1)
        self.assertEqual(workspace.linearity_fits["d13C"]["n"], 7)
        self.assertEqual(workspace.linearity_fits["d18O"]["n"], 8)
        self.assertEqual(sum(len(t.get("customdata", [])) for t in workspace.figures["crossplot"]["data"]), 8)

    def test_linearity_and_manual_preview_exclude_flags_per_isotope(self):
        from services.irms_api.metrology.residual_preview import residual_previews
        rows = [dict(id=str(i), sequence=i, role="qc", material_id="m", label="QC",
                     d13c=2*i+4, d18o=3*i-2, i44_v=i+1, sample_reference_difference_v=i,
                     pressure_mismatch_v=10*i, mass_ug=100, co2_pressure_ubar=None)
                for i in range(1, 9)]
        rows[-1]["d13c"] = 1000
        before = copy.deepcopy(rows)
        result = diagnostics(rows, {"d13c": {"8"}})
        carbon = result["materials"][0]["isotopes"]["d13c"]
        oxygen = result["materials"][0]["isotopes"]["d18o"]
        self.assertAlmostEqual(carbon["intensity_dependence"]["slope"], 2)
        self.assertEqual(carbon["intensity_dependence"]["n"], 7)
        self.assertEqual(oxygen["intensity_dependence"]["n"], 8)
        self.assertEqual(len(carbon["intensity_dependence"]["points"]), 8)
        self.assertTrue(carbon["intensity_dependence"]["points"][-1]["excluded_from_fit"])
        preview = residual_previews(result, {"m:intensity_dependence:d13c": {"enabled": True}})["m:intensity_dependence:d13c"]
        self.assertAlmostEqual(preview["model"]["slope"], 2)
        self.assertAlmostEqual(preview["after"]["sd"], 0, places=10)
        self.assertEqual(preview["n"], 7)
        self.assertEqual(rows, before)

    def test_consultation_spatial_charts_keep_outliers_without_changing_fits(self):
        import pandas as pd
        from services.irms_api.domain.calibration.workspace import build_calibration_workspace
        frame = pd.DataFrame({"Identifier 1":["SHP2L"]*8, "Identifier 2":[""]*8,
                              "d 13C/12C  Mean":[0,0,0,0,0,0,0,9], "d 18O/16O  Mean":[0]*8,
                              "1  Cycle Int  Samp  44":[5]*8})
        config = {"selected_standards":["SHP2L"], "calibration_type":"IQR", "iqr_multiplier":1.5}
        original = build_calibration_workspace("test",frame,{},config)
        station = build_calibration_workspace("test",frame,{"metrology_link":{"results_session_id":"test"}},config)
        for key in ("crossplot", "calibration_3d"):
            self.assertEqual(sum(len(t.get("customdata",[])) for t in original.figures[key]["data"]),7)
            self.assertEqual(sum(len(t.get("customdata",[])) for t in station.figures[key]["data"]),8)
        self.assertEqual(original.linearity_fits, station.linearity_fits)
        self.assertEqual(original.precision_summaries, station.precision_summaries)

    def test_sample_reference_fit_does_not_use_pressure_adjustment(self):
        rows=[dict(id=str(i),sequence=i,role="qc",material_id="m",label="QC",d13c=2*i+4,d18o=3*i-2,i44_v=i+1,reference_i44_v=1,sample_reference_difference_v=i,pressure_mismatch_v=10*i,mass_ug=100,co2_pressure_ubar=None) for i in range(1,8)]
        fits=diagnostics(rows)["materials"][0]["isotopes"]["d13c"]
        self.assertAlmostEqual(fits["sample_reference_dependence"]["slope"],2)
        self.assertAlmostEqual(fits["pressure_dependence"]["slope"],.2)
        self.assertAlmostEqual(fits["intensity_dependence"]["slope"],2)

    def test_aragonite_conversion_scales_uncertainty_and_retains_raw(self):
        model=anchor_model([-10,2],[-10,2],[.01,.01,.02,.02])
        row=dict(role="unknown",d18o=-3,d13c=1)
        original=copy.deepcopy(row)
        base=process_value(row,"d18o",model,{"input_basis":"already_vpdb"})
        converted=process_value(row,"d18o",model,{"input_basis":"already_vpdb","carbonate_material":"aragonite"},monte_carlo=True)
        factor=1.0087/1.0091
        self.assertAlmostEqual(converted["value"],997*factor-1000)
        self.assertAlmostEqual(converted["u_norm"],base["u_norm"]*factor)
        self.assertAlmostEqual(converted["jacobian"][0],base["jacobian"][0]*factor)
        self.assertTrue(converted["processing"]["carbonate"]["applied"])
        self.assertEqual(row,original)
        retained=process_value({**row,"d18o":converted["value"]},"d18o",model,{"input_basis":"already_vpdb","carbonate_material":"aragonite","carbonate_correction_preapplied":True})
        self.assertEqual(retained["value"],converted["value"])
        self.assertFalse(retained["processing"]["carbonate"]["applied"])
        self.assertAlmostEqual(retained["u_norm"],converted["u_norm"])
        qc=process_value({**row,"role":"qc"},"d18o",model,{"input_basis":"already_vpdb","carbonate_material":"aragonite"})
        self.assertEqual(qc["value"],-3)
        self.assertNotIn("carbonate",qc["processing"])

    def test_cycle_evidence_maps_units_and_retains_missing_observations(self):
        source={"mapping":{"cycle":"Cycle Number","i44_v":"Sample","reference_i44_v":"Reference","cycle_d13c":"C"},"source_units":{"Sample":"mV","Reference":"mV"}}
        raw=[{"sheet_row":i+3,"values":{"Cycle Number":str(i+1),"Sample":2000 if i==0 else None,"Reference":1000,"C":-2}} for i in range(2)]
        evidence=analysis_evidence({"raw_rows":raw},source)
        self.assertEqual(evidence["cycles"][0]["i44_v"],2)
        self.assertIsNone(evidence["cycles"][1]["i44_v"])
        self.assertEqual(evidence["raw_rows"],raw)

    def test_isodat_wide_cycles_are_read_without_fabricating_delta_values(self):
        evidence=analysis_evidence({"raw_rows":[{"sheet_row":1,"values":{"1 Cycle Int Samp 44":2400,"1 Cycle Int Ref 44":2300}}]}, {"source_kind":"isodat_raw"})
        self.assertEqual(evidence["cycles"][0]["i44_v"],2.4)
        self.assertNotIn("cycle_d13c",evidence["cycles"][0])

if __name__=="__main__": unittest.main()
