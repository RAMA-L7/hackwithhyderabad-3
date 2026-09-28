"""HTTP transport for OpenAI-compatible chat endpoints (stdlib only).

Contract: return (status, body text) whenever the server answered, including 4xx/5xx; raise
TimeoutError or OSError when nothing came back. The router is written against this contract, so tests
swap in a fake without touching the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

Transport = Callable[[str, str, dict, float], "tuple[int, str]"]


def http_post(url: str, api_key: str, payload: dict, timeout_s: float) -> tuple[int, str]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TimeoutError("request timed out") from exc
        raise
