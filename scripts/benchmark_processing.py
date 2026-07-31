"""Repeatable processing-workspace latency and payload benchmark.

Run from the repository root:

    python scripts/benchmark_processing.py
    python scripts/benchmark_processing.py --session-id <id> --repeats 5
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from services.irms_api.api import main as api_main  # noqa: E402


def _largest_session_id() -> str:
    candidates: list[tuple[int, str]] = []
    for snapshot_path in api_main.store.root_dir.glob("*/snapshot.csv"):
        try:
            size = int(snapshot_path.stat().st_size)
        except OSError:
            continue
        candidates.append((size, snapshot_path.parent.name))
    if not candidates:
        raise SystemExit(f"No sessions with snapshots were found under {api_main.store.root_dir}")
    return max(candidates)[1]


def _measure(session_id: str, species_filter: set[str] | None, repeats: int) -> tuple[list[float], float]:
    durations: list[float] = []
    payload_mb = 0.0
    for _ in range(repeats):
        started = time.perf_counter()
        workspace = api_main._build_processing_workspace_response(
            session_id,
            species_section_filter=species_filter,
        )
        durations.append(time.perf_counter() - started)
        payload_mb = len(workspace.model_dump_json()) / 1_000_000
    return durations, payload_mb


def _print_result(label: str, durations: list[float], payload_mb: float) -> None:
    print(
        f"{label:<20} "
        f"first={durations[0]:.4f}s "
        f"median={statistics.median(durations):.4f}s "
        f"min={min(durations):.4f}s "
        f"payload={payload_mb:.3f}MB"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", help="Saved session ID; defaults to the largest snapshot")
    parser.add_argument("--repeats", type=int, default=3, help="Measurements per workspace shape")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    session_id = args.session_id or _largest_session_id()
    if not api_main.store.session_exists(session_id):
        raise SystemExit(f"Unknown session: {session_id}")

    api_main._clear_processing_workspace_cache(session_id)
    api_main.store._clear_session_frame_cache(session_id)
    base_durations, base_payload = _measure(session_id, set(), args.repeats)
    base_workspace = api_main._build_processing_workspace_response(
        session_id,
        species_section_filter=set(),
    )
    first_species = next(
        (section.species for section in base_workspace.species_sections if section.identifier_count > 0),
        None,
    )

    print(f"session={session_id} measurements={base_workspace.summary.total_measurements}")
    _print_result("base workspace", base_durations, base_payload)
    if first_species:
        section_durations, section_payload = _measure(session_id, {first_species}, args.repeats)
        _print_result(f"section {first_species}", section_durations, section_payload)


if __name__ == "__main__":
    main()
