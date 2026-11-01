"""Report library API: what reports the caller may run, and running them (JSON, CSV or Excel).

`GET /reports` lists the reports the caller's role may run (the `reports` module plus the module the
report's data belongs to). `GET /reports/{key}` runs one: the filters are the query parameters the
catalog lists for it, and the rows are always cut down to the caller's plants. `format=csv`,
`format=xlsx` or `format=pdf` returns a file (at most 50,000 rows, 5,000 for a PDF; narrow the
filters if there are more); the JSON
answer is limited by `limit` (default 1,000, at most 5,000) and says when it was cut. Every run is
recorded in the audit log with who ran it, the filters and the number of rows.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel

from heyyerp_api.masters.common import (
    Ctx,
    audit,
    require_in_scope,
    requires_module,
    rows,
    scope_clause,
)
from heyyerp_api.reports.export import to_csv, to_xlsx
from heyyerp_api.reports.pdf import to_pdf
from heyyerp_api.reports.registry import BY_KEY, REPORTS, Param, Report

router = APIRouter(prefix="/reports", tags=["reports"])
read = requires_module("reports")
EXPORT_MAX = 50_000
PDF_MAX = 5_000  # a printed report is for reading, not for bulk data
FILE_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class ParamInfo(BaseModel):
    name: str
    type: str
    description: str


class ColumnInfo(BaseModel):
    key: str
    label: str
    type: str


class ReportInfo(BaseModel):
    key: str
    title: str
    description: str
    module: str
    params: list[ParamInfo]
    columns: list[ColumnInfo]


class ReportRun(BaseModel):
    key: str
    title: str
    generated_at: datetime
    params: dict[str, str]
    columns: list[ColumnInfo]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


def allowed(ctx: Ctx, report: Report) -> bool:
    return report.module in ctx.principal.profile.modules


def info(report: Report) -> ReportInfo:
    return ReportInfo(
        key=report.key, title=report.title, description=report.description, module=report.module,
        params=[ParamInfo(name=p.name, type=p.kind, description=p.description)
                for p in report.params],
        columns=[ColumnInfo(key=c.key, label=c.label, type=c.kind) for c in report.columns])


def parse(param: Param, raw: str) -> Any:
    try:
        if param.kind == "date":
            return date.fromisoformat(raw)
        if param.kind == "uuid":
            return uuid.UUID(raw)
        if param.kind == "int":
            return int(raw)
    except ValueError as exc:
        raise HTTPException(422, f"{param.name}: not a valid {param.kind}") from exc
    if not raw.strip() or len(raw) > 80:
        raise HTTPException(422, f"{param.name}: give a value of up to 80 characters")
    return raw.strip().upper() if param.name in ("status", "item_type") else raw.strip()


@router.get("", response_model=list[ReportInfo])
async def catalog(ctx: Ctx = Depends(read)) -> list[ReportInfo]:
    """The reports this user may run, with their filters and columns."""
    return [info(r) for r in REPORTS if allowed(ctx, r)]


@router.get("/{key}", response_model=None)
async def run(
    key: str, request: Request, file_format: Literal["json", "csv", "xlsx", "pdf"] = Query(
        "json", alias="format"), limit: int = Query(1000, ge=1, le=5000),
    ctx: Ctx = Depends(read),
) -> Response | ReportRun:
    report = BY_KEY.get(key)
    if report is None or not allowed(ctx, report):
        raise HTTPException(status.HTTP_404_NOT_FOUND if report is None else
                            status.HTTP_403_FORBIDDEN,
                            "No such report" if report is None else
                            f"Your role has no access to {report.module} reports")
    known = {p.name: p for p in report.params}
    sent = {k: v for k, v in request.query_params.items() if k not in ("format", "limit")}
    unknown = sorted(set(sent) - set(known))
    if unknown:
        raise HTTPException(422, f"Unknown filter(s): {', '.join(unknown)}; this report takes "
                                 f"{', '.join(known) or 'none'}")
    values = {name: parse(known[name], raw) for name, raw in sent.items()}
    if "unit_id" in values:
        require_in_scope(ctx, values["unit_id"])
    conditions = [report.where]
    clause, params = scope_clause(ctx, report.unit_column)
    if clause:
        conditions.append(clause)
    conditions += [known[name].fragment for name in values]
    cap = limit if file_format == "json" else PDF_MAX if file_format == "pdf" else EXPORT_MAX
    sql = (f"{report.select} WHERE {' AND '.join(conditions)}"
           f"{' GROUP BY ' + report.group_by if report.group_by else ''} "
           f"ORDER BY {report.order_by} LIMIT :_cap")
    data = await rows(ctx, sql, {**params, **values, "_cap": cap + 1})
    truncated = len(data) > cap
    data = data[:cap]
    if truncated and file_format != "json":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"More than {cap} rows; narrow the filters")
    await audit(ctx, "report.run", "report", key,
                {"format": file_format, "params": {k: str(v) for k, v in values.items()},
                 "rows": len(data)})
    if file_format == "json":
        return ReportRun(
            key=key, title=report.title, generated_at=datetime.now(UTC),
            params={k: str(v) for k, v in values.items()},
            columns=[ColumnInfo(key=c.key, label=c.label, type=c.kind) for c in report.columns],
            rows=data, row_count=len(data), truncated=truncated)
    now = datetime.now(UTC)
    if file_format == "csv":
        body = to_csv(report.columns, data)
    elif file_format == "xlsx":
        body = to_xlsx(report.title, report.columns, data)
    else:
        body = to_pdf(report.title, {k: str(v) for k, v in values.items()}, report.columns,
                      data, now)
    return Response(
        content=body, media_type=FILE_TYPES[file_format],
        headers={"Content-Disposition":
                 f'attachment; filename="{key}_{date.today():%Y%m%d}.{file_format}"',
                 "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
