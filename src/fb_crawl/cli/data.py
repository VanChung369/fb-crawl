from __future__ import annotations

import argparse
import glob
from pathlib import Path

from fb_crawl.exporters.data_merge import (
    read_merge_rows,
    write_merged_csv,
    write_quality_report,
)
from fb_crawl.services.data_merge import DataMergeService


def add_data_parser(mode_subparsers) -> None:
    data = mode_subparsers.add_parser(
        "data",
        help="Merge and validate crawl output data",
    )
    actions = data.add_subparsers(dest="data_action", required=True)
    merge = actions.add_parser(
        "merge",
        help="Merge unified user CSV files and write a quality report",
    )
    merge.add_argument("inputs", nargs="+", help="CSV paths or glob patterns")
    merge.add_argument(
        "--output",
        type=Path,
        default=Path("runtime/output/users-master.csv"),
    )
    merge.add_argument(
        "--report",
        type=Path,
        default=Path("runtime/output/quality-report.json"),
    )


def _input_paths(values: list[str], output: Path) -> tuple[Path, ...]:
    found: list[Path] = []
    seen: set[str] = set()
    output_key = str(output.resolve()).casefold()

    for value in values:
        matches = [Path(item) for item in glob.glob(value)]

        if not matches and Path(value).is_file():
            matches = [Path(value)]

        for path in matches:
            if not path.is_file():
                continue

            key = str(path.resolve()).casefold()

            if key == output_key or key in seen:
                continue

            seen.add(key)
            found.append(path)

    if not found:
        raise ValueError("No readable CSV merge inputs were found.")

    return tuple(found)


def execute_data(args: argparse.Namespace) -> int:
    if args.data_action != "merge":
        raise ValueError(f"Unsupported data action: {args.data_action}")

    paths = _input_paths(args.inputs, args.output)
    loaded = read_merge_rows(paths)

    if not loaded.input_files:
        raise ValueError("No unified user CSV merge inputs were found.")

    result = DataMergeService().run(
        loaded.rows,
        input_files=loaded.input_files,
        skipped_files=loaded.skipped_files,
    )
    write_merged_csv(result, args.output)
    write_quality_report(result, args.report)
    report = result.report
    print(
        f"inputs={report.input_files} rows={report.rows_read} "
        f"records={report.records_written} "
        f"duplicates_merged={report.duplicates_merged} "
        f"conflicts={report.conflicts} "
        f"repair_candidates={report.repair_candidates} "
        f"output={args.output} report={args.report}"
    )
    return 0
