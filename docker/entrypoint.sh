#!/bin/sh
set -eu

interval="${TRANSLATOR_LOOP_INTERVAL_SECONDS:-300}"

while true; do
  python -m ttrss_feed_translator --once
  sleep "$interval"
done

