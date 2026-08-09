from fb_crawl.cli.app import build_parser


def test_pipeline_migrate_parser_contract() -> None:
    args = build_parser().parse_args(["pipeline", "migrate"])

    assert args.mode == "pipeline"
    assert args.pipeline_command == "migrate"


def test_pipeline_retry_parser_defaults() -> None:
    args = build_parser().parse_args(["pipeline", "retry"])

    assert args.mode == "pipeline"
    assert args.pipeline_command == "retry"
    assert args.limit == 20
    assert args.cooldown_hours == 24
    assert args.force is False
    assert args.dry_run is False


def test_pipeline_retry_parser_accepts_controls() -> None:
    args = build_parser().parse_args(
        [
            "pipeline",
            "retry",
            "--limit",
            "7",
            "--cooldown-hours",
            "0",
            "--force",
            "--dry-run",
        ]
    )

    assert args.limit == 7
    assert args.cooldown_hours == 0
    assert args.force is True
    assert args.dry_run is True
