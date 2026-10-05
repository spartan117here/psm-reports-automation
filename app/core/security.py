"""Security utilities for credential masking, token sanitization, and redaction."""

import re
from typing import Any, Dict, List, Set, Union

DEFAULT_SENSITIVE_PATTERNS = {
    r"password",
    r"passwd",
    r"secret",
    r"token",
    r"session_?id",
    r"jsessionid",
    r"cookie",
    r"authorization",
    r"auth_?header",
    r"api_?key",
}

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in DEFAULT_SENSITIVE_PATTERNS]


def is_sensitive_key(key: str) -> bool:
    """Check if a given dictionary key or field name matches sensitive patterns."""
    return any(pattern.search(key) for pattern in _COMPILED_PATTERNS)


def mask_secret(value: str, visible_prefix: int = 1, visible_suffix: int = 1) -> str:
    """Mask a secret string, leaving minimal boundary characters for debugging."""
    if not value:
        return "[EMPTY]"
    length = len(value)
    if length <= (visible_prefix + visible_suffix):
        return "[REDACTED]"
    return f"{value[:visible_prefix]}{'*' * (length - visible_prefix - visible_suffix)}{value[-visible_suffix:]}"


def sanitize_payload(obj: Any) -> Any:
    """
    Recursively sanitize dictionaries and lists, redacting sensitive fields.
    Returns a deep safe copy without mutating inputs.
    """
    if isinstance(obj, dict):
        sanitized = {}
        for k, v in obj.items():
            if isinstance(k, str) and is_sensitive_key(k):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_payload(v)
        return sanitized
    elif isinstance(obj, list):
        return [sanitize_payload(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(sanitize_payload(item) for item in obj)
    return obj
