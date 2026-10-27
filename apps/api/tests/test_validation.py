import pytest

from heyyerp_api.masters import validation as v


def test_known_real_gstin_passes_and_a_changed_digit_fails():
    assert v.valid_gstin("27AAPFU0939F1ZV")  # widely published sample GSTIN
    assert not v.valid_gstin("27AAPFU0939F1ZW")  # wrong check character
    assert not v.valid_gstin("00AAPFU0939F1ZV")  # no such state
    assert not v.valid_gstin("27aapfu0939f1zv")  # callers must normalise first


def test_brd_placeholder_gstin_is_rejected_as_a_placeholder():
    assert not v.valid_gstin("06AAKCV4123R1ZQ")  # BRD demo value, not a real GSTIN


def test_pan_format():
    assert v.valid_pan("AAPFU0939F")
    assert not v.valid_pan("AAPFU0939")
    assert not v.valid_pan("1APFU0939F")


def test_codes_are_upper_cased_and_restricted():
    assert v.clean_code(" p1-main ") == "P1-MAIN"
    for bad in ("", "a b", "x" * 21, "-P1", "P/1"):
        with pytest.raises(ValueError):
            v.clean_code(bad)


def test_blank_identifiers_become_none():
    assert v.clean_gstin("  ") is None and v.clean_gstin(None) is None
    assert v.clean_pan("") is None


def test_state_is_derived_from_gstin_and_conflicts_are_rejected():
    assert v.reconcile_state("27AAPFU0939F1ZV", None) == "27"
    assert v.reconcile_state(None, "06") == "06"
    assert v.reconcile_state(None, None) is None
    with pytest.raises(ValueError):
        v.reconcile_state("27AAPFU0939F1ZV", "06")
    with pytest.raises(ValueError):
        v.reconcile_state(None, "00")
