"""SBCMail utility functions.

Borrowed and adapted from chekusu/mails extract-code pattern.
Supports multi-language verification code extraction (EN/CN/JP/KR).
"""

import re
from typing import Optional


# Patterns that typically surround verification/OTP codes
_CODE_PATTERNS = [
    # Explicit code labels (EN)
    r"(?:verification|confirm|auth|security|one[- ]?time|otp|login|access)\s*(?:code|pin|number|token)\s*(?:is|:)?\s*[:\-]?\s*(\d{4,8})",
    r"(?:code|pin|otp)\s*(?:is|:)?\s*[:\-]?\s*(\d{4,8})",
    # Explicit code labels (CN)
    r"(?:验证码|校验码|确认码|动态码|安全码)\s*(?:是|为|：|:)?\s*(\d{4,8})",
    # Explicit code labels (JP)
    r"(?:認証コード|確認コード|ワンタイムパスワード)\s*(?:は|：|:)?\s*(\d{4,8})",
    # Explicit code labels (KR)
    r"(?:인증\s*코드|확인\s*코드|인증\s*번호)\s*(?:는|은|：|:)?\s*(\d{4,8})",
    # Standalone code in brackets/parentheses
    r"[【\[（\(]\s*(\d{4,8})\s*[】\]）\)]",
    # "Your code: 123456" pattern
    r"(?:your|the)\s+\w*\s*(?:code|pin|otp)\s*(?:is|:)?\s*[:\-]?\s*(\d{4,8})",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _CODE_PATTERNS]


def extract_code(text: str) -> Optional[str]:
    """Extract a verification/OTP code from message text.

    Searches the text for common verification code patterns in
    English, Chinese, Japanese, and Korean.

    Args:
        text: The message text (or payload body) to search.

    Returns:
        The extracted code string (digits only), or None if not found.

    Example:
        >>> extract_code("Your verification code is 847293")
        '847293'
        >>> extract_code("验证码：582901")
        '582901'
    """
    if not text:
        return None

    for pattern in _COMPILED_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)

    return None


def extract_code_from_message(msg) -> Optional[str]:
    """Extract a verification code from a PollMessage or dict.

    Searches payload["body"], payload["text"], and payload["content"] fields.
    Falls back to searching the entire payload as a string.
    """
    payload = msg.payload if hasattr(msg, "payload") else msg
    if not isinstance(payload, dict):
        return extract_code(str(payload))

    # Try common text fields first
    for key in ("body", "text", "content", "message"):
        val = payload.get(key)
        if isinstance(val, str):
            code = extract_code(val)
            if code:
                return code

    # Fallback: search entire payload as string
    return extract_code(str(payload))
