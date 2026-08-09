from fb_crawl.adapters.browser.message_parser import MessageParser


def test_message_parser_extracts_visible_text_sender_and_timestamp() -> None:
    records = MessageParser().parse(
        """
        <main>
          <div data-message-id="mid.1" data-sender-name="Synthetic User"
               data-message-text="Hello from a fixture">
            <a href="/synthetic.user">Synthetic User</a>
            <time datetime="2026-08-08T10:00:00+07:00">10:00</time>
          </div>
        </main>
        """,
        source_url="https://www.facebook.com/messages/t/123",
    )

    assert len(records) == 1
    assert records[0].message_id == "mid.1"
    assert records[0].sender_name == "Synthetic User"
    assert records[0].sender_profile_url == (
        "https://www.facebook.com/synthetic.user"
    )
    assert records[0].text == "Hello from a fixture"
    assert records[0].sent_at == "2026-08-08T10:00:00+07:00"


def test_message_parser_generates_stable_capture_id_and_deduplicates() -> None:
    html = """
    <div data-testid="message-container" data-sender-name="A">
      <span dir="auto">Visible message</span>
    </div>
    """
    parser = MessageParser()
    first = parser.parse(html, source_url="https://www.facebook.com/messages/t/1")
    second = parser.parse(html, source_url="https://www.facebook.com/messages/t/1")

    assert first == second
    assert first[0].message_id.startswith("visible-")


def test_message_parser_skips_rows_without_visible_content() -> None:
    assert MessageParser().parse(
        "<div data-message-id='empty'><button>Like</button></div>",
        source_url="https://www.facebook.com/messages/t/1",
    ) == ()


def test_message_parser_accepts_current_role_row_shape() -> None:
    records = MessageParser().parse(
        """
        <main>
          <div role="row">
            <h4>Current Sender</h4>
            <div dir="auto">Current visible message</div>
            <abbr data-tooltip-content="Saturday 10:00">10:00</abbr>
          </div>
        </main>
        """,
        source_url="https://www.facebook.com/messages/t/123",
    )

    assert len(records) == 1
    assert records[0].sender_name == "Current Sender"
    assert records[0].text == "Current visible message"
    assert records[0].sent_at == "Saturday 10:00"


def test_message_parser_does_not_duplicate_nested_container_shapes() -> None:
    records = MessageParser().parse(
        """
        <div role="row">
          <div data-message-id="mid.nested" data-sender-name="Sender"
               data-message-text="Nested message"></div>
        </div>
        """,
        source_url="https://www.facebook.com/messages/t/123",
    )

    assert [record.message_id for record in records] == ["mid.nested"]
