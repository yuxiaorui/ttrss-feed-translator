from __future__ import annotations

import logging
import re
from urllib.parse import quote

import requests

from ttrss_feed_translator.config import AppConfig


logger = logging.getLogger(__name__)
_MERCURY_URI_PATTERN = re.compile(r"^[0-9A-Za-z!#$%&'()*+,\-./:;=?@\[\]_~]*$")
_MERCURY_URI_SAFE_CHARS = ";/?:@&=+$,-_.!~*'()#[]"


class FulltextError(RuntimeError):
    pass


class MercuryFulltextClient:
    def __init__(self, config: AppConfig):
        self._api_base_url = config.mercury_fulltext_api_base_url.rstrip("/")
        self._timeout = config.mercury_fulltext_request_timeout_seconds
        self._session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self._api_base_url)

    def fetch_content(self, article_url: str) -> str | None:
        if not self.enabled:
            return None

        normalized_url = article_url.strip()
        if not normalized_url:
            return None

        request_url = f"{self._api_base_url}/parser?url={_encode_mercury_url_value(normalized_url)}"
        try:
            response = self._session.get(request_url, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise FulltextError(f"mercury_fulltext request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise FulltextError("mercury_fulltext returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise FulltextError(f"unexpected mercury_fulltext payload: {payload!r}")

        content = payload.get("content")
        if content is None:
            logger.debug("mercury_fulltext response did not include content for url=%s", normalized_url)
            return None
        if not isinstance(content, str):
            raise FulltextError(f"unexpected mercury_fulltext content type: {type(content).__name__}")
        if not content.strip():
            logger.debug("mercury_fulltext returned empty content for url=%s", normalized_url)
            return None
        return content


def _encode_mercury_url_value(url: str) -> str:
    prepared = url if _MERCURY_URI_PATTERN.fullmatch(url) else quote(url, safe=_MERCURY_URI_SAFE_CHARS)
    return quote(prepared, safe="")
