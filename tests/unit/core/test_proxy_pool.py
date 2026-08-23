from __future__ import annotations

import time
from pathlib import Path

from fb_crawl.core.proxy_pool import (
    ProxyEntry,
    ProxyPool,
    ProxyStatus,
    RotationStrategy,
    parse_proxy_line,
)
from fb_crawl.adapters.browser.session_pool import SessionPool


def test_parse_proxy_line_various_formats() -> None:
    # URL format
    p1 = parse_proxy_line("http://192.168.1.1:8080")
    assert p1 is not None
    assert p1.scheme == "http"
    assert p1.host == "192.168.1.1"
    assert p1.port == 8080

    # SOCKS5 with auth
    p2 = parse_proxy_line("socks5://user:pass@10.0.0.1:1080")
    assert p2 is not None
    assert p2.scheme == "socks5"
    assert p2.username == "user"
    assert p2.password == "pass"

    # ip:port format
    p3 = parse_proxy_line("172.16.0.1:3128")
    assert p3 is not None
    assert p3.host == "172.16.0.1"
    assert p3.port == 3128
    assert p3.scheme == "http"

    # ip:port:user:pass format
    p4 = parse_proxy_line("172.16.0.1:3128:admin:secret")
    assert p4 is not None
    assert p4.host == "172.16.0.1"
    assert p4.port == 3128
    assert p4.username == "admin"
    assert p4.password == "secret"

    # Comments and blanks
    assert parse_proxy_line("# this is a comment") is None
    assert parse_proxy_line("   ") is None
    assert parse_proxy_line("invalid_url_without_port") is None


def test_proxy_pool_load_from_file_and_rotate(tmp_path: Path) -> None:
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text(
        "# Proxies list\n"
        "http://1.1.1.1:8080\n"
        "http://2.2.2.2:8080\n"
        "\n"
        "socks5://3.3.3.3:1080\n",
        encoding="utf-8",
    )

    pool = ProxyPool(file_path=proxy_file, strategy=RotationStrategy.ROUND_ROBIN)
    assert pool.total_count == 3
    assert pool.active_count == 3

    p1 = pool.get_proxy()
    p2 = pool.get_proxy()
    p3 = pool.get_proxy()
    p4 = pool.get_proxy()

    assert p1.host == "1.1.1.1"
    assert p2.host == "2.2.2.2"
    assert p3.host == "3.3.3.3"
    assert p4.host == "1.1.1.1"


def test_proxy_pool_failure_and_cooldown() -> None:
    pool = ProxyPool(
        ["http://1.1.1.1:8080", "http://2.2.2.2:8080"],
        default_cooldown_seconds=600.0,
        max_consecutive_failures=2,
    )

    p1 = pool.get_proxy()
    # Mark temporary failure
    pool.mark_failure(p1, cooldown_seconds=600.0)
    assert pool.active_count == 1
    assert pool.get_proxy().host == "2.2.2.2"

    # Mark second failure -> reaches max_consecutive_failures -> DEAD
    pool.mark_failure(p1)
    entry_1 = next(e for e in pool._entries if e.host == "1.1.1.1")
    assert entry_1.status == ProxyStatus.DEAD


def test_session_pool_integrates_with_proxy_pool(tmp_path: Path) -> None:
    s1 = tmp_path / "acc1.json"
    s2 = tmp_path / "acc2.json"
    s1.write_text("[]", encoding="utf-8")
    s2.write_text("[]", encoding="utf-8")

    proxy_pool = ProxyPool(["http://10.0.0.1:8080", "http://10.0.0.2:8080"])
    session_pool = SessionPool([s1, s2], proxy_pool=proxy_pool)

    sess1 = session_pool.next_session()
    sess2 = session_pool.next_session()

    assert sess1 is not None
    assert sess2 is not None
    assert sess1.proxy in {"http://10.0.0.1:8080", "http://10.0.0.2:8080"}
    assert sess2.proxy in {"http://10.0.0.1:8080", "http://10.0.0.2:8080"}
