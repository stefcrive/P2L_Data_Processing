"""Import laboratory examples without changing their bytes or approving a method."""
from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from pathlib import Path

from .importer import parse_workbook
from .models import MethodConfig, QualificationCommand, RunCommand
from .pipeline import identify
from .repository import Repository
from .service import Service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routine", type=Path)
    parser.add_argument("--carousel", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    service = Service(Repository(args.data_dir))
    for kind, path in (("routine", args.routine), ("qualification", args.carousel)):
        if path is None:
            continue
        content = path.read_bytes()
        with service.repo.connect() as db:
            duplicates = service.repo.list(db, "raw_imports", sha256=hashlib.sha256(content).hexdigest())
        if duplicates:
            print(f"Already imported: {path.name}")
            continue
        qualification = None
        method_id = None
        if kind == "qualification":
            state = service.state()
            method = next((m for m in reversed(state["methods"]) if m["status"] == "draft"), state["active_method"])
            if method is None:
                raise ValueError("Create a method draft before importing a carousel")
            method_id = method["id"]
            config = MethodConfig.model_validate(method["config"])
            with service.repo.connect() as db:
                materials = service.material_map(db, method)
            parsed = parse_workbook(content, path.name)
            slots = Counter()
            for row in parsed["measurements"]:
                _, material_id = identify(row, config, materials)
                if material_id and row["mass_ug"] is not None:
                    slots[(material_id, row["mass_ug"])] += 1
            qualification = service.create_qualification(QualificationCommand(actor=args.actor, reason=args.reason,
                method_id=method_id, carousel=[{"material_id": key[0], "mass_ug": key[1], "replicates": count} for key, count in slots.items()]))
        run = service.import_run(path.name, content, RunCommand(actor=args.actor, reason=args.reason, context=kind,
            method_id=method_id, qualification_id=qualification["id"] if qualification else None))
        print(f"Imported {path.name}: {run['analysis_count']} analyses, {run['status']}, run {run['id']}")


if __name__ == "__main__":
    main()
