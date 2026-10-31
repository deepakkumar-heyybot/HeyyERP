"""Employee master, reporting lines and leave types (BRD FR-HR-01, FR-HR-02).

An employee belongs to a plant and a department, may report to another employee (a reporting line
that cannot loop back on itself) and may be linked to the person's sign-in user so that their own
leave and appraisal reach them. Employees are never deleted: leaving the company marks them EXITED
with the date (and is refused while others still report to them). Needs hr.manage to change;
reading needs the hr module; anyone signed in can read their own record at /hr/me. The standard
leave types (CL 7, SL 7, EL 15 days a year and unpaid LOP) are created by a seed that adds only
what is missing.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

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
from heyyerp_api.security import has_permission

router = APIRouter(prefix="/hr", tags=["hr"])
manage = requires("hr.manage")
apply_leave = requires("leave.apply")
read = requires_module("hr")
CODE = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
STANDARD_LEAVE = (
    ("CL", "Casual leave", Decimal(7), False), ("SL", "Sick leave", Decimal(7), False),
    ("EL", "Earned leave", Decimal(15), False), ("LOP", "Loss of pay", Decimal(0), True))


class EmployeeIn(Strict):
    unit_id: UUID
    department_id: UUID
    employee_no: str = CODE
    full_name: str = Field(min_length=2, max_length=200)
    job_title: str | None = Field(default=None, max_length=120)
    manager_id: UUID | None = None
    user_id: UUID | None = None
    date_joined: date | None = None


class EmployeePatch(Strict):
    unit_id: UUID | None = None
    department_id: UUID | None = None
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    job_title: str | None = Field(default=None, max_length=120)
    manager_id: UUID | None = None
    user_id: UUID | None = None


class ExitIn(Strict):
    date_left: date


class EmployeeOut(BaseModel):
    employee_id: UUID
    unit_id: UUID
    department_id: UUID
    department_name: str
    manager_id: UUID | None
    manager_name: str | None
    user_id: UUID | None
    employee_no: str
    full_name: str
    job_title: str | None
    employment_status: str
    date_joined: date | None
    date_left: date | None
    direct_reports: int


class TeamMember(BaseModel):
    employee_id: UUID
    employee_no: str
    full_name: str
    job_title: str | None
    manager_id: UUID | None
    depth: int


class LeaveTypeIn(Strict):
    code: str = CODE
    name: str = Field(min_length=2, max_length=80)
    annual_default_days: Decimal = Field(ge=0, le=366, max_digits=8, decimal_places=2)
    unpaid: bool = False


class LeaveTypePatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    annual_default_days: Decimal | None = Field(default=None, ge=0, le=366, max_digits=8,
                                                decimal_places=2)
    active: bool | None = None


class LeaveTypeOut(BaseModel):
    leave_type_id: UUID
    code: str
    name: str
    annual_default_days: Decimal
    unpaid: bool
    active: bool


EMP_SQL = (
    "SELECT e.employee_id, e.unit_id, e.department_id, d.name AS department_name, e.manager_id, "
    "m.full_name AS manager_name, e.user_id, e.employee_no, e.full_name, e.job_title, "
    "e.employment_status, e.date_joined, e.date_left, (SELECT count(*) FROM erp.employee r WHERE "
    "r.manager_id = e.employee_id AND r.employment_status = 'ACTIVE') AS direct_reports FROM "
    "erp.employee e JOIN erp.department d ON d.department_id = e.department_id LEFT JOIN "
    "erp.employee m ON m.employee_id = e.manager_id"
)


async def load(ctx: Ctx, employee_id: UUID) -> EmployeeOut:
    row = await one(ctx, f"{EMP_SQL} WHERE e.employee_id = :id", {"id": employee_id})
    if row is None or not ctx.principal.may_see_unit(row["unit_id"]):
        raise not_found("Employee")
    return EmployeeOut(**row)


async def my_employee(ctx: Ctx) -> dict[str, Any] | None:
    """The employee record linked to the signed-in user, if there is one."""
    return await one(
        ctx, "SELECT employee_id, unit_id, department_id, manager_id, employment_status FROM "
        "erp.employee WHERE user_id = :u", {"u": ctx.principal.profile.app_user_id})


async def is_manager_of(ctx: Ctx, employee_id: UUID) -> bool:
    """True when the signed-in user is the employee's manager, directly or higher up."""
    found = await one(
        ctx, "WITH RECURSIVE up AS (SELECT manager_id, 1 AS d FROM erp.employee WHERE employee_id "
        "= :e UNION ALL SELECT x.manager_id, up.d + 1 FROM erp.employee x JOIN up ON "
        "x.employee_id = up.manager_id WHERE up.d < 12) SELECT 1 AS x FROM up JOIN erp.employee m "
        "ON m.employee_id = up.manager_id WHERE m.user_id = :u LIMIT 1",
        {"e": employee_id, "u": ctx.principal.profile.app_user_id})
    return found is not None


async def may_view(ctx: Ctx, employee: dict[str, Any]) -> bool:
    """HR (hr.manage within their plants), the employee, or anyone above them in the line."""
    if has_permission(ctx.principal, "hr.manage") and ctx.principal.may_see_unit(
            employee["unit_id"]):
        return True
    mine = await my_employee(ctx)
    if mine is not None and mine["employee_id"] == employee["employee_id"]:
        return True
    return await is_manager_of(ctx, employee["employee_id"])


async def check_department(ctx: Ctx, department_id: UUID, unit_id: UUID) -> None:
    dept = await one(ctx, "SELECT unit_id, active FROM erp.department WHERE department_id = :id",
                     {"id": department_id})
    if dept is None or not dept["active"]:
        raise HTTPException(422, "The department does not exist or is inactive")
    if dept["unit_id"] is not None and dept["unit_id"] != unit_id:
        raise HTTPException(422, "The department belongs to another plant")


async def check_manager(ctx: Ctx, manager_id: UUID, employee_id: UUID | None) -> None:
    boss = await one(ctx, "SELECT employment_status FROM erp.employee WHERE employee_id = :id",
                     {"id": manager_id})
    if boss is None or boss["employment_status"] != "ACTIVE":
        raise HTTPException(422, "The manager must be an active employee")
    if employee_id is not None:
        loop = await one(
            ctx, "WITH RECURSIVE up AS (SELECT employee_id, manager_id, 1 AS d FROM erp.employee "
            "WHERE employee_id = :m UNION ALL SELECT x.employee_id, x.manager_id, up.d + 1 FROM "
            "erp.employee x JOIN up ON x.employee_id = up.manager_id WHERE up.d < 50) SELECT 1 "
            "AS x FROM up WHERE employee_id = :e LIMIT 1", {"m": manager_id, "e": employee_id})
        if loop is not None or manager_id == employee_id:
            raise HTTPException(422, "That would make the reporting line loop back on itself")


async def check_user(ctx: Ctx, user_id: UUID, employee_id: UUID | None) -> None:
    user = await one(ctx, "SELECT status FROM erp.app_user WHERE user_id = :id", {"id": user_id})
    if user is None or user["status"] != "ACTIVE":
        raise HTTPException(422, "The user does not exist or is not active")
    taken = await one(ctx, "SELECT employee_id FROM erp.employee WHERE user_id = :u AND "
                      "employee_id IS DISTINCT FROM :e", {"u": user_id, "e": employee_id})
    if taken is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That user is already linked to an employee")


@router.post("/employees", response_model=EmployeeOut, status_code=status.HTTP_201_CREATED)
async def create_employee(body: EmployeeIn, ctx: Ctx = Depends(manage)) -> EmployeeOut:
    require_in_scope(ctx, body.unit_id)
    await check_department(ctx, body.department_id, body.unit_id)
    if body.manager_id is not None:
        await check_manager(ctx, body.manager_id, None)
    if body.user_id is not None:
        await check_user(ctx, body.user_id, None)
    with db_errors("An employee with this number already exists"):
        created = await one(
            ctx, "INSERT INTO erp.employee(tenant_id, unit_id, department_id, manager_id, user_id, "
            "employee_no, full_name, job_title, date_joined) VALUES (erp.current_tenant_id(), :u, "
            ":d, :m, :user, :no, :n, :t, :j) RETURNING employee_id",
            {"u": body.unit_id, "d": body.department_id, "m": body.manager_id,
             "user": body.user_id, "no": body.employee_no.upper(), "n": body.full_name,
             "t": body.job_title, "j": body.date_joined})
    assert created is not None
    await audit(ctx, "employee.create", "employee", created["employee_id"],
                {"employee_no": body.employee_no})
    return await load(ctx, created["employee_id"])


@router.patch("/employees/{employee_id}", response_model=EmployeeOut)
async def update_employee(
    employee_id: UUID, body: EmployeePatch, ctx: Ctx = Depends(manage)
) -> EmployeeOut:
    current = await load(ctx, employee_id)
    changes = body.model_dump(exclude_unset=True)
    unit = changes.get("unit_id") or current.unit_id
    if "unit_id" in changes:
        if changes["unit_id"] is None:
            raise HTTPException(422, "The plant cannot be empty")
        require_in_scope(ctx, unit)
    if "department_id" in changes or "unit_id" in changes:
        await check_department(ctx, changes.get("department_id") or current.department_id, unit)
    if changes.get("manager_id") is not None:
        await check_manager(ctx, changes["manager_id"], employee_id)
    if changes.get("user_id") is not None:
        await check_user(ctx, changes["user_id"], employee_id)
    if current.employment_status != "ACTIVE" and changes:
        raise HTTPException(status.HTTP_409_CONFLICT, "An exited employee cannot be changed")
    if changes:
        sets = ", ".join(f"{k} = :{k}" for k in changes)
        await rows(ctx, f"UPDATE erp.employee SET {sets} WHERE employee_id = :id RETURNING "
                   "employee_id", {**changes, "id": employee_id})
    await audit(ctx, "employee.update", "employee", employee_id, changes)
    return await load(ctx, employee_id)


@router.post("/employees/{employee_id}/exit", response_model=EmployeeOut)
async def exit_employee(employee_id: UUID, body: ExitIn, ctx: Ctx = Depends(manage)) -> EmployeeOut:
    current = await load(ctx, employee_id)
    if current.employment_status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "The employee has already left")
    if current.direct_reports:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{current.direct_reports} active employee(s) still report to this person; "
            "give them another manager first")
    if body.date_left > date.today() or (current.date_joined and body.date_left < (
            current.date_joined)):
        raise HTTPException(422, "The date left is in the future or before the joining date")
    await rows(ctx, "UPDATE erp.employee SET employment_status = 'EXITED', date_left = :d "
               "WHERE employee_id = :id RETURNING employee_id",
               {"d": body.date_left, "id": employee_id})
    await audit(ctx, "employee.exit", "employee", employee_id, {"date_left": body.date_left})
    return await load(ctx, employee_id)


@router.post("/employees/{employee_id}/reinstate", response_model=EmployeeOut)
async def reinstate_employee(employee_id: UUID, ctx: Ctx = Depends(manage)) -> EmployeeOut:
    current = await load(ctx, employee_id)
    if current.employment_status != "EXITED":
        raise HTTPException(status.HTTP_409_CONFLICT, "The employee is not marked as left")
    await rows(ctx, "UPDATE erp.employee SET employment_status = 'ACTIVE', date_left = NULL WHERE "
               "employee_id = :id RETURNING employee_id", {"id": employee_id})
    await audit(ctx, "employee.reinstate", "employee", employee_id, {})
    return await load(ctx, employee_id)


@router.get("/employees", response_model=Page[EmployeeOut])
async def list_employees(
    unit_id: UUID | None = None, department_id: UUID | None = None, manager_id: UUID | None = None,
    employment_status: str | None = Query(None, alias="status"), q: str | None = Query(None),
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[EmployeeOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "e.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (
            ("e.unit_id = :u", "u", unit_id), ("e.department_id = :d", "d", department_id),
            ("e.manager_id = :m", "m", manager_id),
            ("e.employment_status = :s", "s",
             employment_status.upper() if employment_status else None),
            ("(e.full_name ILIKE :q OR e.employee_no ILIKE :q)", "q", f"%{q}%" if q else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    data = await rows(ctx, f"{EMP_SQL}{where} ORDER BY e.employee_no LIMIT :limit OFFSET :offset",
                      {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.employee e{where}", params)
    return Page(items=[EmployeeOut(**d) for d in data], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/me", response_model=EmployeeOut)
async def me(ctx: Ctx = Depends(apply_leave)) -> EmployeeOut:
    mine = await my_employee(ctx)
    if mine is None:
        raise not_found("Employee record for your user")
    row = await one(ctx, f"{EMP_SQL} WHERE e.employee_id = :id", {"id": mine["employee_id"]})
    assert row is not None
    return EmployeeOut(**row)


@router.get("/employees/{employee_id}", response_model=EmployeeOut)
async def get_employee(employee_id: UUID, ctx: Ctx = Depends(read)) -> EmployeeOut:
    return await load(ctx, employee_id)


@router.get("/employees/{employee_id}/team", response_model=list[TeamMember])
async def team(employee_id: UUID, ctx: Ctx = Depends(apply_leave)) -> list[TeamMember]:
    """Everyone below the employee in the reporting line (HR, the person, or their managers)."""
    head = await one(ctx, "SELECT employee_id, unit_id FROM erp.employee WHERE employee_id = :id",
                     {"id": employee_id})
    if head is None or not await may_view(ctx, head):
        raise not_found("Employee")
    data = await rows(
        ctx, "WITH RECURSIVE down AS (SELECT employee_id, employee_no, full_name, job_title, "
        "manager_id, 1 AS depth FROM erp.employee WHERE manager_id = :e AND employment_status = "
        "'ACTIVE' UNION ALL SELECT x.employee_id, x.employee_no, x.full_name, x.job_title, "
        "x.manager_id, down.depth + 1 FROM erp.employee x JOIN down ON x.manager_id = "
        "down.employee_id WHERE x.employment_status = 'ACTIVE' AND down.depth < 12) SELECT * FROM "
        "down ORDER BY depth, employee_no", {"e": employee_id})
    return [TeamMember(**d) for d in data]


# ------------------------------------------------------------------ leave types
LT_SQL = "SELECT leave_type_id, code, name, annual_default_days, unpaid, active FROM erp.leave_type"


@router.get("/leave-types", response_model=list[LeaveTypeOut])
async def list_leave_types(ctx: Ctx = Depends(apply_leave)) -> list[LeaveTypeOut]:
    return [LeaveTypeOut(**d) for d in await rows(ctx, f"{LT_SQL} ORDER BY code", {})]


@router.post("/leave-types", response_model=LeaveTypeOut, status_code=status.HTTP_201_CREATED)
async def create_leave_type(body: LeaveTypeIn, ctx: Ctx = Depends(manage)) -> LeaveTypeOut:
    with db_errors("A leave type with this code already exists"):
        created = await one(
            ctx, "INSERT INTO erp.leave_type(tenant_id, code, name, annual_default_days, unpaid) "
            "VALUES (erp.current_tenant_id(), :c, :n, :d, :u) RETURNING leave_type_id, code, "
            "name, annual_default_days, unpaid, active",
            {"c": body.code.upper(), "n": body.name, "d": body.annual_default_days,
             "u": body.unpaid})
    assert created is not None
    await audit(ctx, "leave_type.create", "leave_type", created["leave_type_id"], body.model_dump())
    return LeaveTypeOut(**created)


@router.patch("/leave-types/{leave_type_id}", response_model=LeaveTypeOut)
async def update_leave_type(
    leave_type_id: UUID, body: LeaveTypePatch, ctx: Ctx = Depends(manage)
) -> LeaveTypeOut:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "Nothing to change")
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    row = await one(ctx, f"UPDATE erp.leave_type SET {sets} WHERE leave_type_id = :id RETURNING "
                    "leave_type_id, code, name, annual_default_days, unpaid, active",
                    {**changes, "id": leave_type_id})
    if row is None:
        raise not_found("Leave type")
    await audit(ctx, "leave_type.update", "leave_type", leave_type_id, changes)
    return LeaveTypeOut(**row)


@router.post("/leave-types/seed-default", response_model=list[LeaveTypeOut])
async def seed_leave_types(ctx: Ctx = Depends(manage)) -> list[LeaveTypeOut]:
    """Add the standard types (CL 7, SL 7, EL 15, unpaid LOP) that are not there yet."""
    for code, name, days, unpaid in STANDARD_LEAVE:
        await rows(
            ctx, "INSERT INTO erp.leave_type(tenant_id, code, name, annual_default_days, unpaid) "
            "VALUES (erp.current_tenant_id(), :c, :n, :d, :u) ON CONFLICT (tenant_id, code) DO "
            "NOTHING RETURNING leave_type_id", {"c": code, "n": name, "d": days, "u": unpaid})
    await audit(ctx, "leave_type.seed_default", "leave_type", "standard", {})
    return [LeaveTypeOut(**d) for d in await rows(ctx, f"{LT_SQL} ORDER BY code", {})]
