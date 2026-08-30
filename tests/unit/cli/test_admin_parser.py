from __future__ import annotations

import pytest

from fb_crawl.cli import app


EMAIL = "admin@example.com"


def test_admin_bootstrap_has_email_but_no_password_argument() -> None:
    args = app.build_parser().parse_args(
        ["admin", "bootstrap", "--email", EMAIL]
    )

    assert (args.mode, args.admin_command, args.email) == (
        "admin",
        "bootstrap",
        EMAIL,
    )
    with pytest.raises(SystemExit):
        app.build_parser().parse_args(
            [
                "admin",
                "bootstrap",
                "--email",
                EMAIL,
                "--password",
                "secret-on-process-list",
            ]
        )


def test_admin_purge_parser_has_no_destructive_scope_options() -> None:
    args = app.build_parser().parse_args(
        ["admin", "purge-deleted-accounts"]
    )

    assert (args.mode, args.admin_command) == (
        "admin",
        "purge-deleted-accounts",
    )
    assert not hasattr(args, "account_id")
    assert not hasattr(args, "all")


def test_main_dispatches_admin_mode(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        app,
        "execute_admin",
        lambda args: seen.append(args.admin_command) or 23,
    )

    assert app.main(["admin", "purge-deleted-accounts"]) == 23
    assert seen == ["purge-deleted-accounts"]
