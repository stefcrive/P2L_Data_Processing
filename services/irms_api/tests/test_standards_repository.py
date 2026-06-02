from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from services.irms_api.domain.constants import ISOTYPE_D13C, ISOTYPE_D18O
from services.irms_api.domain.standards import StandardsRepository, ensure_standards_database


class StandardsRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.temp_dir.name) / "standards.csv"
        self.db_path = Path(self.temp_dir.name) / "standards.db"
        self.csv_path.write_text(
            "\n".join(
                [
                    "Standard,Isotopic_Value_Type,Value",
                    "SHP2L,VPDB(13C),-0.77",
                    "SHP2L,VSMOW(18O),-5.75",
                    "NBS18,VPDB(13C),-5.01",
                    "NBS18,VSMOW(18O),-23.01",
                    "NBS19,VPDB(13C),1.95",
                    "NBS19,VSMOW(18O),-2.2",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_default_bootstraps_database_from_csv(self) -> None:
        repository = StandardsRepository.default(path=self.csv_path, db_path=self.db_path)
        self.assertTrue(self.db_path.exists())
        self.assertAlmostEqual(repository.get_true_value("SHP2L", ISOTYPE_D13C), -0.77, places=6)
        self.assertAlmostEqual(repository.get_true_value("NBS18", ISOTYPE_D18O), -23.01, places=6)
        self.assertAlmostEqual(repository.get_true_value("NBS19", ISOTYPE_D18O), -2.2, places=6)

    def test_existing_database_value_is_used_for_true_value(self) -> None:
        ensure_standards_database(db_path=self.db_path, standards_csv_path=self.csv_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                UPDATE standards_official_values
                SET value = 9.99
                WHERE standard = ? AND isotopic_value_type = ?
                """,
                ("SHP2L", ISOTYPE_D13C),
            )
            conn.commit()
        repository = StandardsRepository.default(path=self.csv_path, db_path=self.db_path)
        self.assertAlmostEqual(repository.get_true_value("SHP2L", ISOTYPE_D13C), 9.99, places=6)

    def test_official_values_for_standards_returns_both_isotopes(self) -> None:
        repository = StandardsRepository.default(path=self.csv_path, db_path=self.db_path)
        values = repository.official_values_for_standards(["SHP2L"])
        by_type = {item["isotopic_value_type"]: item["value"] for item in values}
        self.assertEqual(set(by_type.keys()), {ISOTYPE_D13C, ISOTYPE_D18O})
        self.assertAlmostEqual(float(by_type[ISOTYPE_D13C]), -0.77, places=6)
        self.assertAlmostEqual(float(by_type[ISOTYPE_D18O]), -5.75, places=6)

    def test_ensure_standards_database_reinserts_missing_csv_rows(self) -> None:
        ensure_standards_database(db_path=self.db_path, standards_csv_path=self.csv_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DELETE FROM standards_official_values WHERE standard IN (?, ?)", ("NBS18", "NBS19"))
            conn.commit()
        ensure_standards_database(db_path=self.db_path, standards_csv_path=self.csv_path)
        repository = StandardsRepository.default(path=self.csv_path, db_path=self.db_path)
        self.assertIn("NBS18", repository.standards_list())
        self.assertIn("NBS19", repository.standards_list())

    def test_upsert_and_delete_standard_values(self) -> None:
        repository = StandardsRepository.default(path=self.csv_path, db_path=self.db_path)
        repository.upsert_official_value("nbs18", ISOTYPE_D13C, -5.25, source="manual")
        self.assertAlmostEqual(repository.get_true_value("NBS18", ISOTYPE_D13C), -5.25, places=6)
        deleted_rows = repository.delete_standard("NBS18")
        self.assertGreaterEqual(deleted_rows, 1)
        self.assertNotIn("NBS18", repository.standards_list())


if __name__ == "__main__":
    unittest.main()
