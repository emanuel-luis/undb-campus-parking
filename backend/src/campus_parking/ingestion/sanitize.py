"""Make client-supplied JSON storable: Postgres text and jsonb refuse NUL, and the driver's UTF-8
encoding refuses lone surrogates (which `json.loads` happily produces from "\\ud800")."""

import re
from typing import Any

REPLACEMENT = chr(0xFFFD)
_UNSTORABLE = re.compile("[" + chr(0) + chr(0xD800) + "-" + chr(0xDFFF) + "]")


def clean(value: Any) -> Any:
    """Copy of a JSON value with NUL and lone surrogates replaced by U+FFFD, in strings and in
    object keys."""
    if isinstance(value, str):
        return _UNSTORABLE.sub(REPLACEMENT, value)
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {clean(k): clean(v) for k, v in value.items()}
    return value
