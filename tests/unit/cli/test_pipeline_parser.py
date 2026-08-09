from fb_crawl.cli.app import build_parser


def test_pipeline_migrate_parser_contract() -> None:
    args = build_parser().parse_args(["pipeline", "migrate"])

    assert args.mode == "pipeline"
    assert args.pipeline_command == "migrate"
