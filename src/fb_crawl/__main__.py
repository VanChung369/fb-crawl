"""Allow running fb_crawl directly via `python -m fb_crawl`."""

from __future__ import annotations

import sys
from fb_crawl.cli.app import main

if __name__ == "__main__":
    sys.exit(main())
