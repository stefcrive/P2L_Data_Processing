import unittest
from unittest.mock import patch

import pandas as pd

from services.irms_api.domain.processing.core import RangeConfig
from services.irms_api.domain.processing.outliers import build_processing_summary


class ProcessingOutlierCountTests(unittest.TestCase):
    def test_overlapping_failures_are_subtracted_once_and_manual_exclusions_count(self):
        df = pd.DataFrame({"Identifier 1": ["sample"] * 4, "Identifier 2": ["1", "2", "3", "4"]})
        masks = {key: pd.Series([False] * 4) for key in [
            "Statistical", "d13C Range", "d18O Range", "Signal Intensity", "Leak Rate",
            "Failed Sample", "Poor Pressure Adjustment", "Partially Saturated Collectors", "Fully Saturated Collectors", "Manual Override",
        ]}
        for key in ["Statistical", "Signal Intensity", "Leak Rate", "Failed Sample"]:
            masks[key].iloc[0] = True
        masks["Manual Override"].iloc[1] = True
        masks["Partially Saturated Collectors"].iloc[2] = True
        with patch("services.irms_api.domain.processing.outliers.build_category_masks", return_value=masks):
            summary = build_processing_summary(df, RangeConfig(partial_saturated_outliers=False))
            assert summary.final_analyses == 2
            assert next(m.value for m in summary.metrics if m.metric == "Unique Outliers") == 2
            summary = build_processing_summary(df, RangeConfig(partial_saturated_outliers=True))
            assert summary.final_analyses == 1
