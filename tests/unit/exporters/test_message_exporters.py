import csv
import json
from pathlib import Path

from fb_crawl.core.models import MessageRecord, ScrapeResult, ScrapeStats
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


def test_authenticated_writer_dispatches_message_records(tmp_path: Path) -> None:
    output = tmp_path / "messages.csv"
    assert write_authenticated(result(), output, "csv") is True

    header = output.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header == ",".join(MESSAGE_FIELDS)
