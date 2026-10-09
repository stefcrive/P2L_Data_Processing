"""Issuer-sourced carbonate catalogue. Lot verification remains a laboratory decision."""
from pathlib import Path
import hashlib
import json

from .models import AssignedValue, Material

ASSETS = Path(__file__).with_name("reference_assets")
VERSION = "2026-10-09"
IAEA = "https://analytical-reference-materials.iaea.org/"


def catalog():
    sources = json.loads((ASSETS / "sources.json").read_text())

    def value(v=None, u=None, classification="certified", scale="VPDB", notes="", kind="standard"):
        return AssignedValue(value=v, uncertainty=u, uncertainty_type=kind if u is not None else "unset",
                             k=1 if u is not None and kind == "standard" else None,
                             classification=classification, scale=scale, notes=notes)

    def material(code, c, o, file, *, page=None, status="available", notes="", date="", supplier="IAEA", aliases=()):
        url = sources.get(file, f"https://tsapps.nist.gov/srmext/certificates/archives/{file[5:]}")
        content = (ASSETS / file).read_bytes()
        return Material(name=code, catalog_code=code, catalog_version=VERSION, supplier=supplier,
                        aliases=list(dict.fromkeys([code.replace("-", ""), code.replace("-", " "), *aliases])),
                        assigned={"d13c": c, "d18o": o}, certificate=url, issue_date=date,
                        source_url=page or IAEA+code.lower(), availability=status, traceability=notes,
                        documents=[{"filename": file, "sha256": hashlib.sha256(content).hexdigest(), "url": url,
                                    "kind": "issuer_report" if "TECDOC" in file or "NIST" in file else "reference_sheet",
                                    "retrieved_at": VERSION}])

    records = [material("IAEA-603", value(2.46, .01), value(-2.37, .04), "IAEA-603.pdf", date="2026-06-30",
                        notes="Calcite. Certified C and O values, combined standard uncertainties, k=1. Verify the laboratory lot and handling against the certificate.")]
    for code, c, uc, large_u, o, uo in [("IAEA-610", -9.109, .03, .012, -18.834, .045),
                                      ("IAEA-611", -30.795, .04, .013, -4.224, .046),
                                      ("IAEA-612", -36.722, .03, .015, -12.079, .062)]:
        records.append(material(code, value(c, uc, notes=f"u for approximately 100 microgram aliquots; u={large_u} per mille at approximately 10 mg. Other masses require a justified uncertainty."),
                                value(o, uo, "information", notes="Information only; not certified for oxygen calibration."),
                                "IAEA-610-611-612.pdf", date="2020-12-16", notes="Calcium carbonate. Carbon uncertainty defaults to the 100 microgram aliquot stated by the issuer. Oxygen is information only."))
    for code, c, uc, o, uo, status, page in [("NBS18", -5.014, .035, -23.01, .1, "available", "nbs18"),
            ("IAEA-CO-1", 2.492, .030, -2.4, .1, "archived", "iaea-co-1"),
            ("IAEA-CO-8", -5.764, .032, -22.7, .2, "available", "iaea-co-8-2"),
            ("IAEA-CO-9", -47.321, .057, -15.6, .2, "archived", "iaea-co-9")]:
        records.append(material(code, value(c, uc, notes="Issuer table reports SD. Confirm scale realization and uncertainty interpretation before verification.", kind="unset"),
                                value(o, uo, "recommended", notes="Issuer reports interlaboratory SD, not an explicitly assigned combined standard uncertainty.", kind="unset"),
                                "IAEA-TECDOC-825.pdf", page=IAEA+page, status=status,
                                aliases=["NBS 18", "NBS-18"] if code == "NBS18" else [],
                                notes="Values transcribed from the current IAEA product table. The attached TECDOC is the issuer's supporting report, not a current lot certificate; its older values may differ. Confirm the stated SD interpretation before using it in an uncertainty budget."))
    records.append(material("NBS19", value(1.95, 0, "defined"), value(-2.2, 0, "defined"), "NIST-8544.pdf",
                            status="quarantined", aliases=["NBS 19", "NBS-19", "TS-Limestone"], date="2012-01-12",
                            notes="VPDB defining values are exact. NBS19 is quarantined; IAEA-603 is its replacement. Archived NIST report expired 2020-12-31; it is retained as historical evidence."))
    records.append(material("USGS44", value(-42.21, .05, "recommended", "VPDB-LSVEC"),
                            value(classification="unassigned", notes="Not suitable for oxygen isotope calibration."), "USGS44.pdf",
                            supplier="USGS / RSIL", aliases=["USGS 44", "USGS-44"], date="2020-11-16",
                            page="https://www.usgs.gov/media/files/rsil-report-stable-isotopic-composition-reference-material-usgs44",
                            notes="Carbon only, VPDB-LSVEC. Do not mix this assignment with VPDB-only calibration values. Issuer report valid through 2034-12-31."))
    records.append(material("LSVEC", value(-46.6, None, "information", "VPDB-LSVEC"), value(classification="unassigned"),
                            "NIST-8545.pdf", status="not_recommended", date="2023-01-24",
                            notes="Historical carbon scale anchor only. Carbon dioxide uptake changes its carbon isotope ratio; no longer recommended for carbon calibration. Current NIST sheet concerns lithium isotopes."))
    nbs18 = next(m for m in records if m.name == "NBS18")
    nbs18.documents.append({"filename": "NIST-8543.pdf", "sha256": hashlib.sha256((ASSETS / "NIST-8543.pdf").read_bytes()).hexdigest(),
                            "url": sources["NIST-8543.pdf"], "kind": "archived_issuer_report", "retrieved_at": VERSION})
    nbs18.traceability += " Archived NIST RM8543 report uses a different rounded carbon assignment on VPDB-LSVEC and expired in 2020; it does not supersede the IAEA table."
    return records


def seed_catalog(repo):
    """Append records once. Existing laboratory lots and frozen methods are preserved."""
    with repo.connect(write=True) as db:
        existing = repo.list(db, "materials")
        for entry in catalog():
            if any(m.get("catalog_code") == entry.catalog_code for m in existing):
                continue
            placeholder = next((m for m in existing if m["name"] == entry.name and not m.get("verified")
                                and not m.get("certificate") and all(a.get("value") is None for a in m["assigned"].values())), None)
            entry.revision_of = placeholder["id"] if placeholder else None
            record = repo.insert(db, "materials", entry.model_dump())
            for document in entry.documents:
                assert repo.blob((ASSETS / document["filename"]).read_bytes()) == document["sha256"]
            if placeholder:
                for method in repo.list(db, "methods", status="draft"):
                    config = method["config"]
                    if placeholder["id"] not in config["anchor_ids"] and config["qc_id"] != placeholder["id"]:
                        continue
                    config["anchor_ids"] = [record["id"] if i == placeholder["id"] else i for i in config["anchor_ids"]]
                    if config["qc_id"] == placeholder["id"]:
                        config["qc_id"] = record["id"]
                    method["revision"] += 1
                    repo.update(db, "methods", method["id"], method)
            repo.audit(db, "reference_catalog_imported", record["id"], "system", "Import issuer reference data and original PDF; laboratory lot verification remains pending", after=record)
