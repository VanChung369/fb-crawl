from pathlib import Path

import fb_crawl
import fb_data_pipeline


def test_pipeline_package_lives_beside_crawler_package() -> None:
    crawler_dir = Path(fb_crawl.__file__).resolve().parent
    pipeline_dir = Path(fb_data_pipeline.__file__).resolve().parent

    assert pipeline_dir.name == "fb_data_pipeline"
    assert pipeline_dir.parent == crawler_dir.parent

