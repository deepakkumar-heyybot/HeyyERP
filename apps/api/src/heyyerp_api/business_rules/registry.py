"""The V-Rub business rules (BRD section 6.1) as typed settings.

Pure data and validation, no database. Each rule keeps the BRD's key so the BRD, the prototype
and the API speak the same names. A value is stored per tenant in business_setting and changed
by an administrator without a release (BRD BR-11); when none is stored the default below is
the answer V-Rub gave. Rules V-Rub has not answered yet default to None ("awaiting decision")
and are not enforced until someone sets them.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

Kind = Literal["bool", "int", "decimal", "enum"]


@dataclass(frozen=True)
class SettingDef:
    key: str
    kind: Kind
    default: Any
    description: str
    rule: str  # BRD rule id, e.g. RULE-01
    awaiting: bool = False  # V-Rub has not decided yet; None means "not enforced"
    nullable: bool = False
    minimum: int | None = None
    maximum: int | None = None
    options: tuple[str, ...] = ()


SETTINGS: tuple[SettingDef, ...] = (
    SettingDef("grnTolPct", "int", 10, "Excess receipt allowed over the pending PO quantity (%)",
               "RULE-01", minimum=0, maximum=100),
    SettingDef("priceTolPct", "int", 0, "PO versus invoice price difference tolerated (%)",
               "RULE-02", minimum=0, maximum=100),
    SettingDef("requireIndent", "bool", False, "A purchase order must come from an approved indent",
               "RULE-03"),
    SettingDef("cashLimit", "decimal", None, "Cash purchase limit per bill; empty means no limit",
               "RULE-04", awaiting=True, nullable=True, minimum=0),
    SettingDef("minQuotes", "int", None, "Minimum quotations before a purchase order; empty means "
               "not enforced", "RULE-05", awaiting=True, nullable=True, minimum=0, maximum=10),
    SettingDef("valuation", "enum", "FIFO", "Stock valuation method", "RULE-06",
               options=("FIFO",)),
    SettingDef("negativeStock", "bool", False, "Allow stock to go below zero", "RULE-07"),
    SettingDef("issueRule", "enum", "FEFO", "Which lot is issued first", "RULE-08",
               options=("FEFO", "FIFO")),
    SettingDef("countFreq", "enum", "MONTHLY", "Physical stock count frequency", "RULE-09",
               options=("WEEKLY", "MONTHLY", "QUARTERLY")),
    SettingDef("marginMD", "decimal", None, "Quote margin (%) above which the MD must approve; "
               "empty means not enforced", "RULE-10", awaiting=True, nullable=True, minimum=0,
               maximum=100),
    SettingDef("schedFormat", "enum", "EXCEL_AND_EMAIL", "How OEM delivery schedules arrive",
               "RULE-11", options=("EXCEL", "EMAIL", "EXCEL_AND_EMAIL")),
    SettingDef("prodReporting", "enum", "PER_SHIFT", "How production is reported", "RULE-12",
               options=("PER_SHIFT", "DAILY")),
    SettingDef("consumption", "enum", "ACTUAL_ISSUE", "Material consumption: issued against the "
               "batch or work order (no backflush)", "RULE-13",
               options=("ACTUAL_ISSUE", "BACKFLUSH")),
    SettingDef("interUnit", "bool", True, "Inter-plant transfers raise an inter-unit invoice and "
               "e-way bill (separate GSTINs)", "RULE-14"),
    SettingDef("lockDay", "int", 0, "Day of the month postings lock; 0 means the last day",
               "RULE-15", minimum=0, maximum=28),
    SettingDef("prodLockExtraDays", "int", 1, "Days into the next month production postings stay "
               "open", "RULE-15", minimum=0, maximum=10),
    SettingDef("appraisal", "enum", "YEARLY", "Appraisal cycle", "RULE-16",
               options=("YEARLY", "HALF_YEARLY")),
    SettingDef("language", "enum", "EN", "User interface language", "RULE-17", options=("EN",)),
    # Not numbered in the BRD: stated as defaults in use for unanswered questions.
    SettingDef("creditLimitBlock", "bool", False, "Block (not just warn) when a customer is over "
               "the credit limit", "DEFAULT"),
    # Costing: each company chooses how finished goods and cost of sales are valued and booked.
    SettingDef("costMethod", "enum", "ACTUAL", "Finished goods cost: ACTUAL (material issued plus "
               "overhead) or STANDARD (the item's standard cost; the difference goes to production "
               "variance)", "DEFAULT", options=("ACTUAL", "STANDARD")),
    SettingDef("overheadPct", "decimal", "0.00", "Conversion cost added to the material cost of "
               "a production lot (% of material cost)", "DEFAULT", minimum=0, maximum=500),
    SettingDef("cogsTiming", "enum", "ON_DISPATCH", "When cost of sales is booked: when the goods "
               "are dispatched or when the invoice is raised", "DEFAULT",
               options=("ON_DISPATCH", "ON_INVOICE")),
    SettingDef("productionPosting", "bool", True, "Book production to the accounts (raw material "
               "to work in progress to finished goods) in plants that have a chart", "DEFAULT"),
    SettingDef("pmDueSoonDays", "int", 7, "Days before a preventive maintenance falls due that "
               "it is flagged as due soon", "DEFAULT", minimum=0, maximum=90),
    SettingDef("msmePayDays", "int", 45, "Days within which MSME vendors must be paid", "DEFAULT",
               minimum=1, maximum=365),
)

BY_KEY: dict[str, SettingDef] = {s.key: s for s in SETTINGS}


def validate(definition: SettingDef, raw: Any) -> Any:
    """Return the value to store, or raise ValueError with a message for the user.

    Booleans must be real booleans (no "true" strings); decimals are kept as exact strings.
    """
    if raw is None:
        if definition.nullable:
            return None
        raise ValueError(f"{definition.key} cannot be empty")
    if definition.kind == "bool":
        if not isinstance(raw, bool):
            raise ValueError(f"{definition.key} must be true or false")
        return raw
    if definition.kind == "enum":
        if raw not in definition.options:
            raise ValueError(f"{definition.key} must be one of {', '.join(definition.options)}")
        return raw
    if definition.kind == "int":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"{definition.key} must be a whole number")
        number: Decimal | int = raw
    else:
        if isinstance(raw, bool) or not isinstance(raw, int | float | str):
            raise ValueError(f"{definition.key} must be a number")
        try:
            number = Decimal(str(raw))
        except InvalidOperation as exc:
            raise ValueError(f"{definition.key} must be a number") from exc
        if not number.is_finite() or number != number.quantize(Decimal("0.01")):
            raise ValueError(f"{definition.key} must be a number with at most 2 decimals")
    if definition.minimum is not None and number < definition.minimum:
        raise ValueError(f"{definition.key} must be at least {definition.minimum}")
    if definition.maximum is not None and number > definition.maximum:
        raise ValueError(f"{definition.key} must be at most {definition.maximum}")
    return raw if definition.kind == "int" else format(number, "f")
