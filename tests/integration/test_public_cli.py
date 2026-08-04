from pathlib import Path

from fb_crawl.cli.app import main
from fb_crawl.core.models import (
    PageRecord,
    ScrapeResult,
    ScrapeStats,
)


class FakeService:
    def run(self, request):
        return ScrapeResult(
            records=(
                PageRecord(
                    canonical_url=request.targets[0],
                    page_name="Example",
                ),
            ),
            issues=(),
            stats=ScrapeStats(
                requested=1,
                discovered=0,
                succeeded=1,
                failed=0,
            ),
        )


def test_public_page_command_writes_csv_without_network(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "fb_crawl.cli.public.build_public_service",
        lambda settings: FakeService(),
    )

    output = tmp_path / "pages.csv"

    exit_code = main(
        [
            "public",
            "page",
            "https://www.facebook.com/example",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0

    assert "Example" in output.read_text(encoding="utf-8-sig")
