"""The report library: each report is data (its SQL, filters, columns and the module it needs).

A report is a read-only query. The caller needs the `reports` module and the module the data comes
from; every report is cut down to the plants the caller may see. Filters are optional and each adds
one fragment to the query, so only values the caller sends are used and nothing is ever pasted into
the SQL. Add a report by adding an entry here.
"""

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["text", "number", "money", "date", "datetime"]
ParamKind = Literal["date", "uuid", "text", "int"]


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: Kind = "text"


@dataclass(frozen=True)
class Param:
    name: str
    kind: ParamKind
    fragment: str  # added to the WHERE when the caller sends the value
    description: str = ""


@dataclass(frozen=True)
class Report:
    key: str
    title: str
    description: str
    module: str  # the source module the caller must also have
    select: str  # SELECT ... FROM ... (no WHERE, no GROUP BY, no ORDER BY)
    unit_column: str  # the plant column used to cut the report to the caller's plants
    columns: tuple[Column, ...]
    order_by: str
    params: tuple[Param, ...] = field(default_factory=tuple)
    where: str = "TRUE"  # a fixed condition that always applies
    group_by: str = ""


def date_param(name: str, column: str, op: str) -> Param:
    side = "From" if op == ">=" else "Up to"
    return Param(name, "date", f"{column} {op} CAST(:{name} AS date)",
                 f"{side} this date ({column.split('.')[-1]})")


def id_param(name: str, column: str, description: str) -> Param:
    return Param(name, "uuid", f"{column} = CAST(:{name} AS uuid)", description)


def plant(column: str) -> Param:
    return Param("unit_id", "uuid", f"{column} = CAST(:unit_id AS uuid)", "Only this plant")


def text_param(name: str, column: str, description: str) -> Param:
    return Param(name, "text", f"{column} = :{name}", description)


REPORTS: tuple[Report, ...] = (
    Report(
        "sales_register", "Sales register", "Customer invoices in a period, with what is paid.",
        "sales",
        "SELECT v.invoice_no, v.issue_date, c.code AS customer_code, c.name AS customer_name, "
        "u.code AS plant, v.taxable_amount, v.tax_amount, v.taxable_amount + v.tax_amount AS "
        "gross_amount, v.paid_amount, v.taxable_amount + v.tax_amount - v.paid_amount AS "
        "outstanding, v.status FROM erp.customer_invoice v JOIN erp.customer c ON c.customer_id = "
        "v.customer_id JOIN erp.unit u ON u.unit_id = v.unit_id",
        "v.unit_id",
        (Column("invoice_no", "Invoice"), Column("issue_date", "Date", "date"),
         Column("customer_code", "Customer code"), Column("customer_name", "Customer"),
         Column("plant", "Plant"), Column("taxable_amount", "Taxable", "money"),
         Column("tax_amount", "GST", "money"), Column("gross_amount", "Total", "money"),
         Column("paid_amount", "Received", "money"), Column("outstanding", "Outstanding", "money"),
         Column("status", "Status")),
        "v.issue_date DESC, v.invoice_no",
        (date_param("date_from", "v.issue_date", ">="), date_param("date_to", "v.issue_date", "<="),
         id_param("customer_id", "v.customer_id", "Only this customer"),
         text_param("status", "v.status", "ISSUED or CANCELLED"), plant("v.unit_id"))),
    Report(
        "receivables_outstanding", "Receivables outstanding",
        "Invoices still unpaid with how many days they are overdue.", "sales",
        "SELECT v.invoice_no, c.code AS customer_code, c.name AS customer_name, u.code AS plant, "
        "v.issue_date, v.due_date, v.taxable_amount + v.tax_amount - v.paid_amount AS outstanding, "
        "GREATEST(CURRENT_DATE - v.due_date, 0) AS days_overdue FROM erp.customer_invoice v JOIN "
        "erp.customer c ON c.customer_id = v.customer_id JOIN erp.unit u ON u.unit_id = v.unit_id",
        "v.unit_id",
        (Column("invoice_no", "Invoice"), Column("customer_code", "Customer code"),
         Column("customer_name", "Customer"), Column("plant", "Plant"),
         Column("issue_date", "Issued", "date"), Column("due_date", "Due", "date"),
         Column("outstanding", "Outstanding", "money"),
         Column("days_overdue", "Days overdue", "number")),
        "days_overdue DESC, v.invoice_no",
        (id_param("customer_id", "v.customer_id", "Only this customer"), plant("v.unit_id"),
         Param("min_days_overdue", "int", "CURRENT_DATE - v.due_date >= :min_days_overdue",
               "Only invoices at least this many days overdue")),
        "v.status = 'ISSUED' AND v.taxable_amount + v.tax_amount > v.paid_amount"),
    Report(
        "purchase_register", "Purchase register", "Supplier bills in a period.", "finance",
        "SELECT b.supplier_invoice_no, b.invoice_date, v.code AS vendor_code, v.name AS "
        "vendor_name, u.code AS plant, b.taxable_amount, b.tax_amount, b.taxable_amount + "
        "b.tax_amount AS gross_amount, b.paid_amount, b.status FROM erp.supplier_bill b JOIN "
        "erp.vendor v ON v.vendor_id = b.vendor_id JOIN erp.unit u ON u.unit_id = b.unit_id",
        "b.unit_id",
        (Column("supplier_invoice_no", "Supplier invoice"), Column("invoice_date", "Date", "date"),
         Column("vendor_code", "Vendor code"), Column("vendor_name", "Vendor"),
         Column("plant", "Plant"), Column("taxable_amount", "Taxable", "money"),
         Column("tax_amount", "GST", "money"), Column("gross_amount", "Total", "money"),
         Column("paid_amount", "Paid", "money"), Column("status", "Status")),
        "b.invoice_date DESC, b.supplier_invoice_no",
        (date_param("date_from", "b.invoice_date", ">="),
         date_param("date_to", "b.invoice_date", "<="),
         id_param("vendor_id", "b.vendor_id", "Only this vendor"),
         text_param("status", "b.status", "For example BOOKED or PAID"), plant("b.unit_id"))),
    Report(
        "payables_outstanding", "Payables outstanding",
        "Booked supplier bills still to be paid, MSME vendors flagged.", "finance",
        "SELECT b.supplier_invoice_no, v.code AS vendor_code, v.name AS vendor_name, u.code AS "
        "plant, v.msme_flag, b.invoice_date, b.due_date, b.taxable_amount + b.tax_amount - "
        "b.paid_amount AS outstanding, GREATEST(CURRENT_DATE - b.due_date, 0) AS days_overdue "
        "FROM erp.supplier_bill b JOIN erp.vendor v ON v.vendor_id = b.vendor_id JOIN erp.unit u "
        "ON u.unit_id = b.unit_id",
        "b.unit_id",
        (Column("supplier_invoice_no", "Supplier invoice"), Column("vendor_code", "Vendor code"),
         Column("vendor_name", "Vendor"), Column("plant", "Plant"), Column("msme_flag", "MSME"),
         Column("invoice_date", "Invoice date", "date"), Column("due_date", "Due", "date"),
         Column("outstanding", "Outstanding", "money"),
         Column("days_overdue", "Days overdue", "number")),
        "b.due_date, b.supplier_invoice_no",
        (id_param("vendor_id", "b.vendor_id", "Only this vendor"), plant("b.unit_id")),
        "b.status IN ('BOOKED', 'PARTIALLY_PAID')"),
    Report(
        "stock_valuation", "Stock valuation",
        "Quantity and value of stock by item and store (first-in-first-out cost layers).",
        "inventory",
        "SELECT i.code AS item_code, i.name AS item_name, i.item_type, s.code AS store, u.code AS "
        "plant, sum(cl.remaining_qty) AS quantity, sum(cl.remaining_qty * cl.unit_cost) AS value "
        "FROM erp.cost_layer cl JOIN erp.lot l ON l.lot_id = cl.lot_id JOIN erp.item i ON "
        "i.item_id = cl.item_id JOIN erp.store s ON s.store_id = l.store_id JOIN erp.unit u ON "
        "u.unit_id = l.unit_id",
        "l.unit_id",
        (Column("item_code", "Item code"), Column("item_name", "Item"),
         Column("item_type", "Type"), Column("store", "Store"), Column("plant", "Plant"),
         Column("quantity", "Quantity", "number"), Column("value", "Value", "money")),
        "i.code, s.code",
        (plant("l.unit_id"), id_param("store_id", "l.store_id", "Only this store"),
         text_param("item_type", "i.item_type", "RM, WIP, FG, SPARE, ...")),
        "cl.remaining_qty > 0", "i.item_id, i.code, i.name, i.item_type, s.code, u.code"),
    Report(
        "dispatch_register", "Dispatch register", "Goods dispatched to customers.", "sales",
        "SELECT d.dispatch_no, d.dispatched_at, so.so_no, c.code AS customer_code, c.name AS "
        "customer_name, u.code AS plant, d.status, d.cost_amount FROM erp.dispatch d JOIN "
        "erp.sales_order so ON so.so_id = d.so_id JOIN erp.customer c ON c.customer_id = "
        "so.customer_id JOIN erp.unit u ON u.unit_id = d.unit_id",
        "d.unit_id",
        (Column("dispatch_no", "Dispatch"), Column("dispatched_at", "Dispatched", "datetime"),
         Column("so_no", "Sales order"), Column("customer_code", "Customer code"),
         Column("customer_name", "Customer"), Column("plant", "Plant"),
         Column("status", "Status"), Column("cost_amount", "Cost", "money")),
        "d.dispatched_at DESC NULLS LAST, d.dispatch_no",
        (date_param("date_from", "d.dispatched_at::date", ">="),
         date_param("date_to", "d.dispatched_at::date", "<="),
         id_param("customer_id", "so.customer_id", "Only this customer"), plant("d.unit_id"))),
    Report(
        "production_summary", "Production summary",
        "Good pieces, rejects, downtime and yield by day and machine.", "manufacturing",
        "SELECT s.work_date, m.code AS machine, u.code AS plant, sum(s.good_qty) AS good_qty, "
        "sum(s.reject_qty) AS reject_qty, sum(s.downtime_minutes) AS downtime_minutes, "
        "CASE WHEN sum(s.good_qty + s.reject_qty) = 0 THEN NULL ELSE round(100 * sum(s.good_qty) "
        "/ sum(s.good_qty + s.reject_qty), 2) END AS yield_pct FROM erp.shift_production s JOIN "
        "erp.machine m ON m.machine_id = s.machine_id JOIN erp.unit u ON u.unit_id = s.unit_id",
        "s.unit_id",
        (Column("work_date", "Date", "date"), Column("machine", "Machine"),
         Column("plant", "Plant"), Column("good_qty", "Good", "number"),
         Column("reject_qty", "Rejects", "number"),
         Column("downtime_minutes", "Downtime (min)", "number"),
         Column("yield_pct", "Yield %", "number")),
        "s.work_date DESC, m.code",
        (date_param("date_from", "s.work_date", ">="), date_param("date_to", "s.work_date", "<="),
         id_param("machine_id", "s.machine_id", "Only this machine"), plant("s.unit_id")),
        "TRUE", "s.work_date, m.code, u.code"),
    Report(
        "breakdown_register", "Breakdown register", "Machine breakdowns with downtime and cause.",
        "maintenance",
        "SELECT b.breakdown_no, m.code AS machine, u.code AS plant, b.started_at, b.ended_at, "
        "GREATEST(0, floor(extract(epoch FROM (COALESCE(b.ended_at, now()) - b.started_at)) / "
        "60))::int AS downtime_minutes, b.cause, b.corrective_action, b.status FROM "
        "erp.breakdown b JOIN erp.machine m ON m.machine_id = b.machine_id JOIN erp.unit u ON "
        "u.unit_id = b.unit_id",
        "b.unit_id",
        (Column("breakdown_no", "Breakdown"), Column("machine", "Machine"),
         Column("plant", "Plant"), Column("started_at", "Started", "datetime"),
         Column("ended_at", "Ended", "datetime"),
         Column("downtime_minutes", "Downtime (min)", "number"), Column("cause", "Cause"),
         Column("corrective_action", "Action"), Column("status", "Status")),
        "b.started_at DESC",
        (date_param("date_from", "b.started_at::date", ">="),
         date_param("date_to", "b.started_at::date", "<="),
         id_param("machine_id", "b.machine_id", "Only this machine"),
         text_param("status", "b.status", "OPEN or CLOSED"), plant("b.unit_id"))),
    Report(
        "leave_summary", "Leave taken", "Approved leave days by employee and leave type.", "hr",
        "SELECT e.employee_no, e.full_name, u.code AS plant, t.code AS leave_type, "
        "count(*) AS applications, sum(l.requested_days) AS days FROM erp.leave_request l JOIN "
        "erp.employee e ON e.employee_id = l.employee_id JOIN erp.leave_type t ON "
        "t.leave_type_id = l.leave_type_id JOIN erp.unit u ON u.unit_id = e.unit_id",
        "e.unit_id",
        (Column("employee_no", "Employee no"), Column("full_name", "Name"),
         Column("plant", "Plant"), Column("leave_type", "Leave type"),
         Column("applications", "Applications", "number"), Column("days", "Days", "number")),
        "e.employee_no, t.code",
        (date_param("date_from", "l.start_date", ">="), date_param("date_to", "l.start_date", "<="),
         plant("e.unit_id")),
        "l.status = 'APPROVED'", "e.employee_id, e.employee_no, e.full_name, u.code, t.code"),
    Report(
        "kaizen_register", "Kaizen register", "Suggestions with their savings.", "kaizen",
        "SELECT k.kaizen_no, k.title, k.category, dp.name AS department, u.code AS plant, "
        "k.status, k.submitted_at::date AS submitted, k.estimated_saving, k.verified_saving, "
        "COALESCE((SELECT sum(s.amount) FROM erp.kaizen_saving s WHERE s.kaizen_id = "
        "k.kaizen_id), 0) AS realised_saving FROM erp.kaizen k JOIN erp.department dp ON "
        "dp.department_id = k.department_id JOIN erp.unit u ON u.unit_id = k.unit_id",
        "k.unit_id",
        (Column("kaizen_no", "Kaizen"), Column("title", "Title"), Column("category", "Category"),
         Column("department", "Department"), Column("plant", "Plant"),
         Column("status", "Status"), Column("submitted", "Submitted", "date"),
         Column("estimated_saving", "Estimated saving", "money"),
         Column("verified_saving", "Verified saving", "money"),
         Column("realised_saving", "Realised saving", "money")),
        "k.submitted_at DESC",
        (date_param("date_from", "k.submitted_at::date", ">="),
         date_param("date_to", "k.submitted_at::date", "<="),
         text_param("status", "k.status", "For example APPROVED"),
         id_param("department_id", "k.department_id", "Only this department"),
         plant("k.unit_id"))),
)

BY_KEY: dict[str, Report] = {r.key: r for r in REPORTS}
