from __future__ import annotations

import argparse
import glob
from pathlib import Path

from fb_crawl.exporters.data_merge import (
    read_merge_rows,
    write_merged_csv,
    write_quality_report,
)
from fb_crawl.exporters.data_plan import (
    read_plan_rows,
    write_plan_report,
    write_plan_targets,
)
from fb_crawl.exporters.phone_evidence_merge import (
    read_phone_evidence,
    write_phone_evidence_master,
    write_phone_evidence_report,
)
from fb_crawl.services.data_merge import DataMergeService
from fb_crawl.services.data_plan import DataPlanService
from fb_crawl.services.phone_evidence_merge import PhoneEvidenceMergeService


def add_data_parser(mode_subparsers) -> None:
    data = mode_subparsers.add_parser(
        "data",
        help="Plan, merge, and validate crawl output data",
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
    plan = actions.add_parser(
        "plan",
        help="Select missing profiles and write authenticated batch targets",
    )
    plan.add_argument("input", type=Path, help="Unified master user CSV")
    plan.add_argument(
        "--missing",
        default="phone,address,current_city,birth_year",
        help="Comma-separated profile fields to fill",
    )
    plan.add_argument("--limit", type=int, default=100)
    plan.add_argument("--cooldown-days", type=int, default=30)
    plan.add_argument(
        "--failure-cooldown-days",
        type=int,
        default=1,
        help=(
            "Short cooldown for navigation_failed or section_unavailable"
        ),
    )
    plan.add_argument(
        "--force",
        action="store_true",
        help="Ignore last_enriched_at cooldowns for incomplete records",
    )
    plan.add_argument(
        "--skip-repair",
        action="store_true",
        help="Do not select complete rows solely for identity repair",
    )
    plan.add_argument(
        "--output",
        type=Path,
        default=Path("runtime/targets/enrichment.txt"),
    )
    plan.add_argument(
        "--report",
        type=Path,
        default=Path("runtime/output/enrichment-plan.json"),
    )
    phone_merge = actions.add_parser(
        "phone-merge",
        help="Merge phone evidence CSV files and write a quality report",
    )
    phone_merge.add_argument(
        "inputs",
        nargs="+",
        help="Phone evidence CSV paths or glob patterns",
    )
    phone_merge.add_argument(
        "--default-country-code",
        default="84",
        help="Country calling code used for local numbers; default: 84",
    )
    phone_merge.add_argument(
        "--output",
        type=Path,
        default=Path("runtime/output/phone-evidence-master.csv"),
    )
    phone_merge.add_argument(
        "--report",
        type=Path,
        default=Path("runtime/output/phone-evidence-quality.json"),
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


def _execute_merge(args: argparse.Namespace) -> int:
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


def _missing_fields(value: str) -> tuple[str, ...]:
    fields = tuple(
        dict.fromkeys(
            item.strip().casefold()
            for item in value.split(",")
            if item.strip()
        )
    )

    if not fields:
        raise ValueError("At least one missing field is required.")

    return fields


def _execute_plan(args: argparse.Namespace) -> int:
    rows = read_plan_rows(args.input)
    result = DataPlanService().run(
        rows,
        missing_fields=_missing_fields(args.missing),
        limit=args.limit,
        cooldown_days=args.cooldown_days,
        failure_cooldown_days=args.failure_cooldown_days,
        force=args.force,
        include_repair=not args.skip_repair,
    )
    write_plan_targets(result, args.output)
    write_plan_report(
        result,
        args.report,
        input_path=args.input,
    )
    report = result.report
    print(
        f"rows={report.input_rows} eligible={report.eligible} "
        f"selected={report.selected} limited={report.limited} "
        f"skipped_recent={report.skipped_recent} "
        f"retry_candidates={report.retry_candidates} "
        f"selected_retry={report.selected_retry_candidates} "
        f"repair_candidates={report.repair_candidates} "
        f"output={args.output} report={args.report}"
    )
    return 0


def _execute_phone_merge(args: argparse.Namespace) -> int:
    paths = _input_paths(args.inputs, args.output)
    loaded = read_phone_evidence(paths)

    if not loaded.input_files:
        raise ValueError("No phone evidence CSV merge inputs were found.")

    result = PhoneEvidenceMergeService().run(
        loaded.rows,
        input_files=loaded.input_files,
        skipped_files=loaded.skipped_files,
        default_country_code=args.default_country_code,
    )
    write_phone_evidence_master(result, args.output)
    write_phone_evidence_report(result, args.report)
    report = result.report
    print(
        f"inputs={report.input_files} rows={report.rows_read} "
        f"records={report.records_written} "
        f"duplicates_merged={report.duplicates_merged} "
        f"invalid_phone={report.invalid_phone_rows} "
        f"missing_uid={report.missing_uid_rows} "
        f"identity_conflicts={report.identity_conflict_rows} "
        f"output={args.output} report={args.report}"
    )
    return 0


def execute_data(args: argparse.Namespace) -> int:
    if args.data_action == "merge":
        return _execute_merge(args)

    if args.data_action == "plan":
        return _execute_plan(args)

    if args.data_action == "phone-merge":
        return _execute_phone_merge(args)

    raise ValueError(f"Unsupported data action: {args.data_action}")
