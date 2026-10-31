"""Fixed asset register and depreciation runs (BRD FR-MNT-04).

An asset has its cost, residual value, the date it was put to use and its life. Two books are kept:
the COMPANIES_ACT book (straight line over the useful life, or written-down value at a rate; posted
to the accounts) and the INCOME_TAX book (written-down value at the tax block rate; a schedule, not
posted). The register records assets; buying one is booked through the supplier bill or voucher, so
registering does not post. A month's depreciation is worked out as everything the asset should have
carried to the end of that month less what earlier runs already took, so a month skipped is
caught up by the next run. Full months take depreciable/life (straight line) or opening
value x rate/12 (written down); a part month is pro-rated by days according to the company rule
`depreciationConvention`. The residual value may not exceed `salvageMaxPct` of cost. Runs go in
month order: a run is DRAFT until posted or cancelled, a month can be run from its last day, and
only the latest run of a book can be cancelled (posting is reversed). The journal is dated the last
day of the month and so follows the month lock; a company that runs late chooses with the rule
`depreciationLateBooking` to book it in the current month instead of being refused. Disposing
of an asset stops its depreciation from the disposal date. Needs asset.manage; reading needs the
finance module.
"""

from calendar import monthrange
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from heyyerp_api.business_rules import service as rules
from heyyerp_api.finance.autopost import book, finance_enabled
from heyyerp_api.finance.journals import ReverseIn, reverse_journal
from heyyerp_api.finance.period import (
    ensure_accounting_open,
    get_period,
    month_start,
)
from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    audit,
    count,
    db_errors,
    not_found,
    one,
    require_in_scope,
    requires,
    requires_module,
    rows,
    scope_clause,
)

router = APIRouter(prefix="/finance", tags=["finance"])
manage = requires("asset.manage")
read = requires_module("finance")
ZERO = Decimal(0)
PAISA = Decimal("0.01")
Books = Literal["COMPANIES_ACT", "INCOME_TAX"]
Cost = Field(ge=0, le=Decimal(10_000_000_000), max_digits=18, decimal_places=2)


class AssetIn(Strict):
    unit_id: UUID
    asset_code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    description: str = Field(min_length=3, max_length=200)
    category: str | None = Field(default=None, max_length=60)
    acquisition_date: date  # the day it was put to use
    acquisition_cost: Decimal = Cost
    salvage_value: Decimal = Field(default=ZERO, ge=0, max_digits=18, decimal_places=2)
    useful_life_months: int = Field(gt=0, le=1200)
    method: Literal["SLM", "WDV"] = "SLM"
    wdv_rate: Decimal | None = Field(default=None, gt=0, le=100)
    it_rate: Decimal | None = Field(default=None, gt=0, le=100)  # Income-tax block rate
    machine_id: UUID | None = None

    @model_validator(mode="after")
    def check(self) -> "AssetIn":
        if self.method == "WDV" and self.wdv_rate is None:
            raise ValueError("A written-down-value asset needs its rate")
        if self.salvage_value > self.acquisition_cost:
            raise ValueError("The residual value is more than the cost")
        return self


class AssetPatch(Strict):
    description: str | None = Field(default=None, min_length=3, max_length=200)
    category: str | None = Field(default=None, max_length=60)
    machine_id: UUID | None = None
    it_rate: Decimal | None = Field(default=None, gt=0, le=100)
    salvage_value: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    useful_life_months: int | None = Field(default=None, gt=0, le=1200)
    wdv_rate: Decimal | None = Field(default=None, gt=0, le=100)


class DisposeIn(Strict):
    disposed_on: date
    disposal_value: Decimal = Field(default=ZERO, ge=0, max_digits=18, decimal_places=2)


class RunIn(Strict):
    unit_id: UUID
    month: date  # any day of the month
    books: Books = "COMPANIES_ACT"


class AssetOut(BaseModel):
    asset_id: UUID
    unit_id: UUID
    asset_code: str
    description: str
    category: str | None
    acquisition_date: date
    acquisition_cost: Decimal
    salvage_value: Decimal
    useful_life_months: int
    method: str
    wdv_rate: Decimal | None
    it_rate: Decimal | None
    machine_id: UUID | None
    status: str
    disposed_on: date | None
    disposal_value: Decimal | None
    accumulated_depreciation: Decimal  # Companies Act book, posted runs only
    book_value: Decimal
    tax_accumulated: Decimal  # Income-tax book
    tax_value: Decimal


ASSET_SQL = (
    "SELECT a.asset_id, a.unit_id, a.asset_code, a.description, a.category, a.acquisition_date, "
    "a.acquisition_cost, a.salvage_value, a.useful_life_months, a.method, a.wdv_rate, a.it_rate, "
    "a.machine_id, a.status, a.disposed_on, a.disposal_value, COALESCE((SELECT sum(l.depreciation_"
    "amount) FROM erp.depreciation_line l JOIN erp.depreciation_run r ON r.depreciation_run_id = "
    "l.depreciation_run_id WHERE l.asset_id = a.asset_id AND r.status = 'POSTED' AND r.books = "
    "'COMPANIES_ACT'), 0) AS accumulated_depreciation, COALESCE((SELECT sum(l.depreciation_amount) "
    "FROM erp.depreciation_line l JOIN erp.depreciation_run r ON r.depreciation_run_id = "
    "l.depreciation_run_id WHERE l.asset_id = a.asset_id AND r.status = 'POSTED' AND r.books = "
    "'INCOME_TAX'), 0) AS tax_accumulated FROM erp.fixed_asset a"
)


def describe(row: dict[str, Any]) -> AssetOut:
    return AssetOut(**row, book_value=row["acquisition_cost"] - row["accumulated_depreciation"],
                    tax_value=row["acquisition_cost"] - row["tax_accumulated"])


async def load(ctx: Ctx, asset_id: UUID) -> AssetOut:
    row = await one(ctx, f"{ASSET_SQL} WHERE a.asset_id = :id", {"id": asset_id})
    if row is None:
        raise not_found("Asset")
    require_in_scope(ctx, row["unit_id"])
    return describe(row)


async def has_depreciation(ctx: Ctx, asset_id: UUID) -> bool:
    found = await one(
        ctx, "SELECT 1 AS x FROM erp.depreciation_line l JOIN erp.depreciation_run r ON "
        "r.depreciation_run_id = l.depreciation_run_id WHERE l.asset_id = :id AND r.status <> "
        "'CANCELLED' LIMIT 1", {"id": asset_id})
    return found is not None


# ------------------------------------------------------------------ the calculation
def in_service_from(acquired: date, convention: str) -> date:
    """The first day the asset carries depreciation, by the company's convention."""
    if convention == "FULL_MONTH":
        return acquired.replace(day=1)
    if convention == "NEXT_MONTH":
        return date(acquired.year + (acquired.month == 12), acquired.month % 12 + 1, 1)
    return acquired


def tax_year_end(day: date) -> date:
    """The last day of the Indian financial year (April to March) the day falls in."""
    return date(day.year + (day.month >= 4), 3, 31)


def carried_to(
    asset: dict[str, Any], books: str, month_end: date, convention: str, half_days: int = 0
) -> Decimal:
    """What the asset should have depreciated in total up to the end of `month_end`'s month.

    In the Income-tax book an asset used for fewer than `half_days` days of the year it was
    bought gets half the rate for that year."""
    start = in_service_from(asset["acquisition_date"], convention)
    cost = asset["acquisition_cost"]
    if books == "INCOME_TAX":
        wdv, rate, depreciable = True, asset["it_rate"], cost
    else:
        wdv = asset["method"] == "WDV"
        rate = asset["wdv_rate"]
        depreciable = cost - asset["salvage_value"]
    if rate is None and wdv:
        return ZERO
    stop = asset["disposed_on"]
    total = ZERO
    half_until = None
    if books == "INCOME_TAX" and half_days > 0:
        year_end = tax_year_end(asset["acquisition_date"])
        if (year_end - asset["acquisition_date"]).days + 1 < half_days:
            half_until = year_end
    year, month = start.year, start.month
    while (year, month) <= (month_end.year, month_end.month) and total < depreciable:
        days_in = monthrange(year, month)[1]
        first, last = date(year, month, 1), date(year, month, days_in)
        if stop is not None and stop < last:
            last = stop
        active = (last - max(first, start)).days + 1
        if active > 0:
            share = Decimal(active) / Decimal(days_in)
            if wdv:
                cut = Decimal("0.5") if half_until and last <= half_until else Decimal(1)
                dep = (cost - total) * rate / 1200 * share * cut
            else:
                dep = depreciable / asset["useful_life_months"] * share
            total += min(dep.quantize(PAISA, rounding=ROUND_HALF_UP), depreciable - total)
        year, month = (year + (month == 12), month % 12 + 1)
    return total


# ------------------------------------------------------------------ register
async def check_salvage(ctx: Ctx, cost: Decimal, salvage: Decimal) -> None:
    limit = Decimal(int(await rules.get_value(ctx, "salvageMaxPct")))
    if salvage > cost * limit / 100:
        raise HTTPException(422, f"The residual value may be at most {limit}% of the cost")


@router.post("/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def register_asset(body: AssetIn, ctx: Ctx = Depends(manage)) -> AssetOut:
    require_in_scope(ctx, body.unit_id)
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    if unit is None or not unit["active"]:
        raise HTTPException(422, "The plant does not exist or is inactive")
    if body.acquisition_date > date.today():
        raise HTTPException(422, "The asset cannot be put to use in the future")
    await check_salvage(ctx, body.acquisition_cost, body.salvage_value)
    if body.machine_id is not None:
        machine = await one(ctx, "SELECT unit_id FROM erp.machine WHERE machine_id = :id",
                            {"id": body.machine_id})
        if machine is None or machine["unit_id"] != body.unit_id:
            raise HTTPException(422, "The machine must be a machine of the same plant")
    with db_errors("This plant already has an asset with this code"):
        created = await one(
            ctx, "INSERT INTO erp.fixed_asset(tenant_id, unit_id, asset_code, description, "
            "category, acquisition_date, acquisition_cost, salvage_value, useful_life_months, "
            "method, wdv_rate, it_rate, machine_id) VALUES (erp.current_tenant_id(), :u, :c, :d, "
            ":cat, :ad, :cost, :s, :life, :m, :w, :it, :mc) RETURNING asset_id",
            {"u": body.unit_id, "c": body.asset_code.upper(), "d": body.description,
             "cat": body.category, "ad": body.acquisition_date, "cost": body.acquisition_cost,
             "s": body.salvage_value, "life": body.useful_life_months, "m": body.method,
             "w": body.wdv_rate, "it": body.it_rate, "mc": body.machine_id})
    assert created is not None
    await audit(ctx, "asset.register", "fixed_asset", created["asset_id"],
                {"asset_code": body.asset_code, "cost": body.acquisition_cost})
    return await load(ctx, created["asset_id"])


@router.patch("/assets/{asset_id}", response_model=AssetOut)
async def update_asset(asset_id: UUID, body: AssetPatch, ctx: Ctx = Depends(manage)) -> AssetOut:
    current = await load(ctx, asset_id)
    changes = body.model_dump(exclude_unset=True)
    money_fields = {"salvage_value", "useful_life_months", "wdv_rate"}
    if money_fields & changes.keys() and await has_depreciation(ctx, asset_id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Depreciation has been run; its basis can no longer change")
    if "salvage_value" in changes:
        await check_salvage(ctx, current.acquisition_cost, changes["salvage_value"])
    if changes.get("wdv_rate") is None and current.method == "WDV" and "wdv_rate" in changes:
        raise HTTPException(422, "A written-down-value asset needs its rate")
    if changes.get("machine_id") is not None:
        machine = await one(ctx, "SELECT unit_id FROM erp.machine WHERE machine_id = :id",
                            {"id": changes["machine_id"]})
        if machine is None or machine["unit_id"] != current.unit_id:
            raise HTTPException(422, "The machine must be a machine of the same plant")
    if changes:
        sets = ", ".join(f"{k} = :{k}" for k in changes)
        await rows(ctx, f"UPDATE erp.fixed_asset SET {sets} WHERE asset_id = :id RETURNING "
                   "asset_id", {**changes, "id": asset_id})
    await audit(ctx, "asset.update", "fixed_asset", asset_id, changes)
    return await load(ctx, asset_id)


async def book_disposal(
    ctx: Ctx, asset: AssetOut, body: DisposeIn
) -> tuple[UUID | None, Decimal]:
    """Post the disposal for a plant with a chart; returns the journal and the depreciation of
    the part month it carried (nothing is posted, and nothing carried, without a chart)."""
    if not await finance_enabled(ctx, asset.unit_id):
        return None, ZERO
    convention = str(await rules.get_value(ctx, "depreciationConvention"))
    month = month_start(body.disposed_on)
    before = date(month.year - (month.month == 1), (month.month - 2) % 12 + 1, 1)
    if in_service_from(asset.acquisition_date, convention) < month:
        done = await one(
            ctx, "SELECT max(p.year_month) AS m FROM erp.depreciation_run r JOIN "
            "erp.fiscal_period p ON p.period_id = r.period_id WHERE r.unit_id = :u AND "
            "r.books = 'COMPANIES_ACT' AND r.status = 'POSTED'", {"u": asset.unit_id})
        if done is None or done["m"] is None or done["m"] < before:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Post the depreciation up to {before:%B %Y} before disposing of this asset")
    row = await one(ctx, "SELECT * FROM erp.fixed_asset WHERE asset_id = :id",
                    {"id": asset.asset_id})
    assert row is not None
    row["disposed_on"] = body.disposed_on
    month_end = date(month.year, month.month, monthrange(month.year, month.month)[1])
    extra = max(ZERO, carried_to(row, "COMPANIES_ACT", month_end, convention)
                - asset.accumulated_depreciation)
    accumulated = asset.accumulated_depreciation + extra
    gain = body.disposal_value - (asset.acquisition_cost - accumulated)
    day, narration = body.disposed_on, f"Disposal of asset {asset.asset_code}"
    try:
        await ensure_accounting_open(ctx, asset.unit_id, body.disposed_on)
    except HTTPException:
        if await rules.get_value(ctx, "depreciationLateBooking") != "BOOK_IN_CURRENT_MONTH":
            raise
        day, narration = date.today(), f"{narration} (booked late)"
    journal_id = await book(
        ctx, unit_id=asset.unit_id, source_type="ASSET_DISPOSAL", source_id=asset.asset_id,
        narration=narration, day=day,
        amounts=[("DEPRECIATION_EXPENSE", extra, ZERO), ("ACCUM_DEPRECIATION", ZERO, extra),
                 ("ACCUM_DEPRECIATION", accumulated, ZERO),
                 ("FIXED_ASSETS", ZERO, asset.acquisition_cost),
                 ("BANK", body.disposal_value, ZERO),
                 ("ASSET_DISPOSAL_RESULT", max(-gain, ZERO), max(gain, ZERO))])
    return journal_id, extra


@router.post("/assets/{asset_id}/dispose", response_model=AssetOut)
async def dispose(asset_id: UUID, body: DisposeIn, ctx: Ctx = Depends(manage)) -> AssetOut:
    """Sold or scrapped: depreciation stops from this date. In a plant with a chart the disposal is
    booked: the part month's depreciation, the asset and its accumulated depreciation taken off,
    the proceeds to the bank and the difference to profit or loss on disposal. That needs the
    depreciation of the earlier months posted first."""
    asset = await load(ctx, asset_id)
    if asset.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "The asset is already disposed")
    if body.disposed_on < asset.acquisition_date or body.disposed_on > date.today():
        raise HTTPException(422, "The disposal date is before it was put to use or in the future")
    posted = await one(
        ctx, "SELECT max(p.year_month) AS m FROM erp.depreciation_run r JOIN erp.fiscal_period p "
        "ON p.period_id = r.period_id WHERE r.unit_id = :u AND r.status <> 'CANCELLED'",
        {"u": asset.unit_id})
    last_month = posted["m"] if posted else None
    if last_month and body.disposed_on <= date(
            last_month.year, last_month.month, monthrange(last_month.year, last_month.month)[1]):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Depreciation has been run beyond that date; cancel those runs first")
    journal_id, extra = await book_disposal(ctx, asset, body)
    await rows(ctx, "UPDATE erp.fixed_asset SET status = 'DISPOSED', disposed_on = :d, "
               "disposal_value = :v, disposal_journal_id = :j, disposal_depreciation = :x "
               "WHERE asset_id = :id RETURNING asset_id",
               {"d": body.disposed_on, "v": body.disposal_value, "j": journal_id, "x": extra,
                "id": asset_id})
    await audit(ctx, "asset.dispose", "fixed_asset", asset_id,
                {"disposed_on": body.disposed_on, "value": body.disposal_value})
    return await load(ctx, asset_id)


@router.get("/assets", response_model=Page[AssetOut])
async def list_assets(
    unit_id: UUID | None = None, asset_status: str | None = Query(None, alias="status"),
    category: str | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[AssetOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "a.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (
            ("a.unit_id = :u", "u", unit_id), ("a.category = :c", "c", category),
            ("a.status = :s", "s", asset_status.upper() if asset_status else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    data = await rows(ctx, f"{ASSET_SQL}{where} ORDER BY a.asset_code LIMIT :limit OFFSET :offset",
                      {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.fixed_asset a{where}", params)
    return Page(items=[describe(d) for d in data], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/assets/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: UUID, ctx: Ctx = Depends(read)) -> AssetOut:
    return await load(ctx, asset_id)


# ------------------------------------------------------------------ depreciation runs
class RunLine(BaseModel):
    asset_id: UUID
    asset_code: str
    depreciation_amount: Decimal
    accumulated_amount: Decimal
    opening_value: Decimal
    closing_value: Decimal


class RunOut(BaseModel):
    depreciation_run_id: UUID
    unit_id: UUID
    month: date
    books: str
    status: str
    journal_id: UUID | None
    total: Decimal
    lines: list[RunLine]


RUN_SQL = (
    "SELECT r.depreciation_run_id, r.unit_id, p.year_month AS month, r.books, r.status, "
    "r.journal_id, COALESCE((SELECT sum(l.depreciation_amount) FROM erp.depreciation_line l WHERE "
    "l.depreciation_run_id = r.depreciation_run_id), 0) AS total FROM erp.depreciation_run r JOIN "
    "erp.fiscal_period p ON p.period_id = r.period_id"
)


async def load_run(ctx: Ctx, run_id: UUID, lines: bool = True) -> RunOut:
    head = await one(ctx, f"{RUN_SQL} WHERE r.depreciation_run_id = :id", {"id": run_id})
    if head is None:
        raise not_found("Depreciation run")
    require_in_scope(ctx, head["unit_id"])
    detail: list[RunLine] = []
    if lines:
        detail = [RunLine(**d) for d in await rows(
            ctx, "SELECT l.asset_id, a.asset_code, l.depreciation_amount, l.accumulated_amount, "
            "l.opening_value, l.closing_value FROM erp.depreciation_line l JOIN erp.fixed_asset a "
            "ON a.asset_id = l.asset_id WHERE l.depreciation_run_id = :id ORDER BY a.asset_code",
            {"id": run_id})]
    return RunOut(**head, lines=detail)


async def latest_run(ctx: Ctx, unit_id: UUID, books: str) -> dict[str, Any] | None:
    return await one(
        ctx, "SELECT r.depreciation_run_id, r.status, p.year_month FROM erp.depreciation_run r "
        "JOIN erp.fiscal_period p ON p.period_id = r.period_id WHERE r.unit_id = :u AND "
        "r.books = :b AND r.status <> 'CANCELLED' ORDER BY p.year_month DESC LIMIT 1",
        {"u": unit_id, "b": books})


@router.post("/depreciation-runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def create_run(body: RunIn, ctx: Ctx = Depends(manage)) -> RunOut:
    """Work out a finished month's depreciation; nothing is booked until it is posted."""
    require_in_scope(ctx, body.unit_id)
    first = month_start(body.month)
    month_end = date(first.year, first.month, monthrange(first.year, first.month)[1])
    if month_end > date.today():
        raise HTTPException(422, "Only a finished month can be depreciated")
    last = await latest_run(ctx, body.unit_id, body.books)
    if last is not None:
        if last["status"] == "DRAFT":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Post or cancel the earlier draft run first")
        if last["year_month"] >= first:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Runs go in month order; this month is already done")
    convention = str(await rules.get_value(ctx, "depreciationConvention"))
    half_days = int(await rules.get_value(ctx, "itHalfRateDays"))
    assets = await rows(
        ctx, "SELECT a.*, COALESCE((SELECT sum(l.depreciation_amount) FROM erp.depreciation_line l "
        "JOIN erp.depreciation_run r ON r.depreciation_run_id = l.depreciation_run_id WHERE "
        "l.asset_id = a.asset_id AND r.status = 'POSTED' AND r.books = :b), 0) AS taken FROM "
        "erp.fixed_asset a WHERE a.unit_id = :u AND a.acquisition_date <= :e AND "
        "(:b = 'INCOME_TAX' OR a.disposal_journal_id IS NULL) ORDER BY a.asset_code",
        {"u": body.unit_id, "b": body.books, "e": month_end})
    period = await get_period(ctx, body.unit_id, first)
    created = await one(
        ctx, "INSERT INTO erp.depreciation_run(tenant_id, unit_id, period_id, books, status, "
        "created_by) VALUES (erp.current_tenant_id(), :u, :p, :b, 'DRAFT', :me) RETURNING "
        "depreciation_run_id",
        {"u": body.unit_id, "p": period["period_id"], "b": body.books,
         "me": ctx.principal.profile.app_user_id})
    assert created is not None
    run_id: UUID = created["depreciation_run_id"]
    for a in assets:
        if body.books == "INCOME_TAX" and a["it_rate"] is None:
            continue
        due = carried_to(a, body.books, month_end, convention, half_days) - a["taken"]
        if due <= 0:
            continue
        accumulated = a["taken"] + due
        await rows(
            ctx, "INSERT INTO erp.depreciation_line(tenant_id, depreciation_run_id, asset_id, "
            "depreciation_amount, accumulated_amount, opening_value, closing_value) VALUES "
            "(erp.current_tenant_id(), :r, :a, :d, :acc, :o, :c) RETURNING depreciation_line_id",
            {"r": run_id, "a": a["asset_id"], "d": due, "acc": accumulated,
             "o": a["acquisition_cost"] - a["taken"], "c": a["acquisition_cost"] - accumulated})
    result = await load_run(ctx, run_id)
    if not result.lines:
        raise HTTPException(status.HTTP_409_CONFLICT, "There is no depreciation to run this month")
    await audit(ctx, "depreciation.run", "depreciation_run", run_id,
                {"month": first.isoformat(), "books": body.books, "total": result.total})
    return result


@router.post("/depreciation-runs/{run_id}/post", response_model=RunOut)
async def post_run(run_id: UUID, ctx: Ctx = Depends(manage)) -> RunOut:
    """Make the run final. The Companies Act book is booked: debit depreciation, credit the
    accumulated depreciation; the Income-tax book is only a schedule."""
    run = await load_run(ctx, run_id, lines=False)
    locked = await one(ctx, "SELECT status FROM erp.depreciation_run WHERE depreciation_run_id = "
                       ":id FOR UPDATE", {"id": run_id})
    if locked is None or locked["status"] != "DRAFT":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft run can be posted")
    journal_id = None
    if run.books == "COMPANIES_ACT":
        if not await finance_enabled(ctx, run.unit_id):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Set up the plant's chart of accounts before posting depreciation")
        month_end = date(run.month.year, run.month.month,
                         monthrange(run.month.year, run.month.month)[1])
        day, narration = month_end, f"Depreciation {run.month:%B %Y}"
        try:
            await ensure_accounting_open(ctx, run.unit_id, month_end)
        except HTTPException:
            if await rules.get_value(ctx, "depreciationLateBooking") != "BOOK_IN_CURRENT_MONTH":
                raise
            day, narration = date.today(), f"Depreciation {run.month:%B %Y} (booked late)"
        journal_id = await book(
            ctx, unit_id=run.unit_id, source_type="DEPRECIATION_RUN", source_id=run_id,
            narration=narration, day=day,
            amounts=[("DEPRECIATION_EXPENSE", run.total, ZERO),
                     ("ACCUM_DEPRECIATION", ZERO, run.total)])
    await rows(ctx, "UPDATE erp.depreciation_run SET status = 'POSTED', posted_at = now(), "
               "journal_id = :j WHERE depreciation_run_id = :id RETURNING depreciation_run_id",
               {"j": journal_id, "id": run_id})
    await audit(ctx, "depreciation.post", "depreciation_run", run_id,
                {"books": run.books, "total": run.total, "journal_id": journal_id})
    return await load_run(ctx, run_id)


@router.post("/depreciation-runs/{run_id}/cancel", response_model=RunOut)
async def cancel_run(run_id: UUID, ctx: Ctx = Depends(manage)) -> RunOut:
    """Undo the latest run of a book: a draft is dropped, a posted one is reversed in the books."""
    run = await load_run(ctx, run_id, lines=False)
    locked = await one(ctx, "SELECT status, journal_id FROM erp.depreciation_run WHERE "
                       "depreciation_run_id = :id FOR UPDATE", {"id": run_id})
    assert locked is not None
    if locked["status"] == "CANCELLED":
        raise HTTPException(status.HTTP_409_CONFLICT, "The run is already cancelled")
    last = await latest_run(ctx, run.unit_id, run.books)
    if last is None or last["depreciation_run_id"] != run_id:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Only the latest run of a book can be cancelled; cancel later first")
    if locked["journal_id"] is not None:
        await reverse_journal(locked["journal_id"], ReverseIn(
            reason=f"Depreciation run {run.month:%B %Y} cancelled"), ctx)
    await rows(ctx, "UPDATE erp.depreciation_run SET status = 'CANCELLED' WHERE "
               "depreciation_run_id = :id RETURNING depreciation_run_id", {"id": run_id})
    await audit(ctx, "depreciation.cancel", "depreciation_run", run_id, {"books": run.books})
    return await load_run(ctx, run_id)


@router.get("/depreciation-runs", response_model=list[RunOut])
async def list_runs(
    unit_id: UUID, books: Books | None = None, ctx: Ctx = Depends(read)
) -> list[RunOut]:
    require_in_scope(ctx, unit_id)
    data = await rows(ctx, f"{RUN_SQL} WHERE r.unit_id = :u AND (CAST(:b AS text) IS NULL OR "
                      "r.books = :b) ORDER BY p.year_month DESC, r.books",
                      {"u": unit_id, "b": books})
    return [await load_run(ctx, d["depreciation_run_id"], lines=False) for d in data]


@router.get("/depreciation-runs/{run_id}", response_model=RunOut)
async def get_run(run_id: UUID, ctx: Ctx = Depends(read)) -> RunOut:
    return await load_run(ctx, run_id)
