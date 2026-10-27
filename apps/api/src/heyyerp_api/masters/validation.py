"""Format checks for Indian business identifiers and master codes."""

import re

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,19}$")
# State code 01-38 plus 97 (other territory) and 99 (centre jurisdiction).
GSTIN_RE = re.compile(
    r"^(0[1-9]|[12][0-9]|3[0-8]|97|99)[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
)
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
STATE_RE = re.compile(r"^(0[1-9]|[12][0-9]|3[0-8]|97|99)$")
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _gstin_check_char(first14: str) -> str:
    total = 0
    for index, char in enumerate(first14):
        product = _ALPHABET.index(char) * (1 if index % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def valid_gstin(value: str) -> bool:
    return bool(GSTIN_RE.match(value)) and _gstin_check_char(value[:14]) == value[14]


def valid_pan(value: str) -> bool:
    return bool(PAN_RE.match(value))


def clean_code(value: str) -> str:
    code = value.strip().upper()
    if not CODE_RE.match(code):
        raise ValueError("Code must be 1-20 characters: A-Z, 0-9, '-' or '_'")
    return code


ITEM_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,29}$")
HSN_RE = re.compile(r"^(\d{4}|\d{6}|\d{8})$")


def clean_item_code(value: str) -> str:
    code = value.strip().upper()
    if not ITEM_CODE_RE.match(code):
        raise ValueError("Item code must be 1-30 characters: A-Z, 0-9, '.', '/', '-' or '_'")
    return code


def clean_hsn(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    hsn = value.strip()
    if not HSN_RE.match(hsn):
        raise ValueError("HSN code must be 4, 6 or 8 digits")
    return hsn


def clean_gstin(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    gstin = value.strip().upper()
    if not valid_gstin(gstin):
        raise ValueError("Invalid GSTIN (format or check digit)")
    return gstin


def clean_pan(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    pan = value.strip().upper()
    if not valid_pan(pan):
        raise ValueError("Invalid PAN")
    return pan


def reconcile_state(gstin: str | None, state_code: str | None) -> str | None:
    """A GSTIN starts with its state code; derive it, and reject a conflicting one."""
    if state_code is not None and not STATE_RE.match(state_code):
        raise ValueError("Invalid GST state code")
    if gstin and state_code and gstin[:2] != state_code:
        raise ValueError("State code does not match the GSTIN")
    return state_code or (gstin[:2] if gstin else None)
