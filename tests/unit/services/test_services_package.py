from __future__ import annotations

import subprocess
import sys


def test_services_package_import_is_side_effect_free() -> None:
    """Break caught: importing one service implicitly loads browser/exporter stacks."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import fb_crawl.services; "
                "assert not any(name == 'selenium' or name.startswith('selenium.') "
                "or name == 'fb_crawl.exporters' "
                "or name.startswith('fb_crawl.exporters.') "
                "for name in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_services_package_preserves_each_public_attribute() -> None:
    """Break caught: the lazy package drops a supported convenience import."""
    import fb_crawl.services as services

    assert services.__all__ == [
        "AuthenticatedService",
        "CheckpointingService",
        "IdentityRepairService",
        "DataMergeService",
        "DataPlanService",
        "PhoneEvidenceMergeService",
    ]
    for name in services.__all__:
        assert getattr(services, name).__name__ == name
