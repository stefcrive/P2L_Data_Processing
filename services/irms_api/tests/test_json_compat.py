from __future__ import annotations

import unittest

import numpy as np

from services.irms_api.domain.shared.json_compat import to_json_compatible


class JsonCompatTests(unittest.TestCase):
    def test_non_finite_numbers_are_converted_to_none(self) -> None:
        payload = {
            "nan_float": float("nan"),
            "pos_inf": float("inf"),
            "neg_inf": float("-inf"),
            "np_nan": np.float64(np.nan),
            "array": np.array([1.0, np.nan, np.inf, -np.inf]),
        }

        result = to_json_compatible(payload)

        self.assertEqual(result["array"], [1.0, None, None, None])
        self.assertIsNone(result["nan_float"])
        self.assertIsNone(result["pos_inf"])
        self.assertIsNone(result["neg_inf"])
        self.assertIsNone(result["np_nan"])


if __name__ == "__main__":
    unittest.main()
