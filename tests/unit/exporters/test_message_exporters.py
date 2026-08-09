import csv
import json
from pathlib import Path

from fb_crawl.core.models import (
    AuthenticatedBatchResult,
    InspectRecord,
    MessageRecord,
    RetryStats,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_crawl.exporters.messages import MESSAGE_FIELDS, write_messages
from fb_crawl.exporters.authenticated import write_authenticated


def result() -> ScrapeResult[MessageRecord]:
    return ScrapeResult(
        records=(
            MessageRecord(
                message_id="mid.1",
                sender_name="Synthetic Sender",
                sender_profile_url="https://www.facebook.com/synthetic.sender",
                text="Visible message",
                sent_at="2026-08-08T10:00:00+07:00",
                thread_url="https://www.facebook.com/messages/t/123",
            ),
        ),
        issues=(),
        stats=ScrapeStats(requested=1, discovered=1, succeeded=1, failed=0),
    )


def test_message_csv_uses_a_separate_conversation_schema(tmp_path: Path) -> None:
    output = tmp_path / "messages.csv"
    assert write_messages(result(), output, "csv") is True

    with output.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    assert tuple(rows[0]) == MESSAGE_FIELDS
    assert rows[0]["text"] == "Visible message"
    assert rows[0]["thread_url"].endswith("/messages/t/123")


def test_message_json_contains_stats_and_visible_records(tmp_path: Path) -> None:
    output = tmp_path / "messages.json"
    assert write_messages(result(), output, "json") is True
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["records"][0]["message_id"] == "mid.1"
    assert payload["stats"]["succeeded"] == 1
    assert payload["enrichment"] is None


def test_message_json_includes_target_retry_coverage(tmp_path: Path) -> None:
    output = tmp_path / "messages.json"
    retry_result = ScrapeResult(
        records=result().records,
        issues=(),
        stats=result().stats,
        retry=RetryStats(
            attempted_targets=1,
            retried=1,
            rate_limited=0,
            pending=0,
        ),
    )

    assert write_messages(retry_result, output, "json") is True
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["retry"]["retried"] == 1


def test_authenticated_writer_dispatches_message_records(tmp_path: Path) -> None:
    output = tmp_path / "messages.csv"
    assert write_authenticated(result(), output, "csv") is True

    header = output.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header == ",".join(MESSAGE_FIELDS)


def test_mixed_batch_splits_user_and_message_outputs(tmp_path: Path) -> None:
    user_result = ScrapeResult(
        records=(
            UserRecord(
                user_id="synthetic.user",
                name="Synthetic User",
                profile_url="https://www.facebook.com/synthetic.user",
                source="profile",
                source_url="https://www.facebook.com/synthetic.user",
            ),
        ),
        issues=(),
        stats=ScrapeStats(requested=1, discovered=1, succeeded=1, failed=0),
    )
    empty_inspect = ScrapeResult[InspectRecord](
        records=(),
        issues=(),
        stats=ScrapeStats(requested=0, discovered=0, succeeded=0, failed=0),
    )
    batch = AuthenticatedBatchResult(
        user_result=user_result,
        message_result=result(),
        inspect_result=empty_inspect,
        stats=ScrapeStats(requested=2, discovered=2, succeeded=2, failed=0),
        issues=(),
    )
    output = tmp_path / "batch.csv"

    assert write_authenticated(batch, output, "csv") is True
    assert output.exists()
    assert (tmp_path / "batch-messages.csv").exists()
