from __future__ import annotations

import argparse
import logging

from ttrss_feed_translator.app import run_once
from ttrss_feed_translator.config import AppConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate TT-RSS entries in PostgreSQL.")
    parser.add_argument("--once", action="store_true", help="Run exactly once.")
    parser.parse_args()

    config = AppConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_once(config)


if __name__ == "__main__":
    main()

