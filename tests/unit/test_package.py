import fb_crawl


def test_package_exposes_version() -> None:
    assert fb_crawl.__version__ == "0.1.0"