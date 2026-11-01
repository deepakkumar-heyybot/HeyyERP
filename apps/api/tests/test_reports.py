"""The report library: access by role, plant scope, filters, exports and audit."""

import io
from decimal import Decimal

import psycopg
import pytest
from openpyxl import load_workbook

from .test_compound_batches import Fresh
from .test_inventory_issue import receive
from .test_inventory_receipt import world
from .test_receivables import invoice, receipt, receivables_world

pytestmark = pytest.mark.usefixtures("api_database")
R = "/api/v1/reports"


def sql(api_database, query, *params):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchone() if cur.description else None


def test_every_report_runs_and_the_catalog_follows_the_role(make_client, api_database):
    head = Fresh(make_client, "PLANT_HEAD")
    listed = head.get(R).json()
    keys = {r["key"] for r in listed}
    assert {"sales_register", "receivables_outstanding", "purchase_register",
            "payables_outstanding", "stock_valuation", "dispatch_register", "production_summary",
            "breakdown_register", "leave_summary", "kaizen_register"} <= keys
    for key in sorted(keys):  # a bad query in any report would show here
        ran = head.get(f"{R}/{key}")
        assert ran.status_code == 200, (key, ran.text)
        body = ran.json()
        assert body["key"] == key and {c["key"] for c in body["columns"]}
        assert all(set(row) == {c["key"] for c in body["columns"]} for row in body["rows"])
    sales_only = {r["key"] for r in Fresh(make_client, "SALES").get(R).json()}
    assert "sales_register" in sales_only and "purchase_register" not in sales_only
    assert "production_summary" not in sales_only and "kaizen_register" in sales_only
    assert Fresh(make_client, "SALES").get(f"{R}/purchase_register").status_code == 403
    assert Fresh(make_client, "STORES").get(f"{R}/sales_register").status_code == 403
    assert Fresh(make_client, "OPERATOR").get(R).status_code == 403  # no reports module
    assert head.get(f"{R}/no_such_report").status_code == 404


def test_the_sales_and_receivables_reports_show_invoices_filters_and_overdue(
        make_client, api_database):
    w = receivables_world(make_client, api_database)
    unit = w["unit"]["unit_id"]
    first, second = invoice(make_client, w, "10"), invoice(make_client, w, "20")  # 2,360 and 4,720
    receipt(make_client, w, "2360", [(first["invoice_id"], "2360")])
    sql(api_database, "update erp.customer_invoice set due_date = current_date - 40 where "
        "invoice_id = %s", second["invoice_id"])
    sales = Fresh(make_client, "SALES")
    register = sales.get(f"{R}/sales_register?unit_id={unit}").json()
    assert register["row_count"] == 2
    by = {r["invoice_no"]: r for r in register["rows"]}
    assert Decimal(by[first["invoice_no"]]["outstanding"]) == 0
    assert Decimal(by[second["invoice_no"]]["gross_amount"]) == Decimal("4720.00")
    assert sales.get(f"{R}/sales_register?unit_id={unit}&status=cancelled").json()["rows"] == []
    assert sales.get(f"{R}/sales_register?unit_id={unit}&date_from=2099-01-01").json()[
        "row_count"] == 0
    assert sales.get(f"{R}/sales_register?unit_id={unit}&customer_id="
                     f"{w['customer']['customer_id']}").json()["row_count"] == 2
    owed = sales.get(f"{R}/receivables_outstanding?unit_id={unit}").json()
    assert [r["invoice_no"] for r in owed["rows"]] == [second["invoice_no"]]  # the paid one is out
    assert owed["rows"][0]["days_overdue"] == 40
    assert sales.get(f"{R}/receivables_outstanding?unit_id={unit}&min_days_overdue=60"
                     ).json()["row_count"] == 0
    cut = sales.get(f"{R}/sales_register?unit_id={unit}&limit=1").json()
    assert cut["row_count"] == 1 and cut["truncated"] is True


def test_stock_valuation_adds_up_the_cost_layers(make_client, api_database):
    w = world(make_client, qty="100", price="10")
    receive(make_client, w, "100")
    store = w["store"]["store_id"]
    got = Fresh(make_client, "STORES").get(f"{R}/stock_valuation?store_id={store}").json()
    assert got["row_count"] == 1
    row = got["rows"][0]
    assert Decimal(row["quantity"]) == 100 and Decimal(row["value"]) == 1000
    assert row["item_code"] == w["item"]["code"] and row["item_type"] == "RM"
    assert Fresh(make_client, "STORES").get(
        f"{R}/stock_valuation?store_id={store}&item_type=fg").json()["rows"] == []


def test_a_user_sees_only_their_plants_and_cannot_ask_for_another(make_client, api_database):
    w = receivables_world(make_client, api_database)
    unit = w["unit"]["unit_id"]
    invoice(make_client, w, "10")
    other = Fresh(make_client, "ADMIN").post("/api/v1/org/units", json={
        "code": "RP" + unit[:5], "name": "Other plant"}).json()
    scoped = Fresh(make_client, "SALES", subject="rep-scoped", unit_ids=[other["unit_id"]])
    assert scoped.get(f"{R}/sales_register").json()["row_count"] == 0  # not their plant's invoice
    assert scoped.get(f"{R}/sales_register?unit_id={unit}").status_code == 403
    assert Fresh(make_client, "SALES").get(f"{R}/sales_register?unit_id={unit}").json()[
        "row_count"] == 1


def test_files_are_exports_that_cannot_run_as_formulas_and_every_run_is_audited(
        make_client, api_database):
    w = receivables_world(make_client, api_database)
    unit = w["unit"]["unit_id"]
    invoice(make_client, w, "10")
    sql(api_database, "update erp.customer set name = '=HYPERLINK(\"http://evil\",\"x\")' where "
        "customer_id = %s", w["customer"]["customer_id"])
    sales = Fresh(make_client, "SALES")
    asked = f"{R}/sales_register?unit_id={unit}"
    csv_file = sales.get(f"{asked}&format=csv")
    assert csv_file.status_code == 200 and "text/csv" in csv_file.headers["content-type"]
    assert "attachment" in csv_file.headers["content-disposition"]
    assert csv_file.headers["x-content-type-options"] == "nosniff"
    text = csv_file.content.decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0].startswith("Invoice,Date,Customer code,Customer,Plant,Taxable")
    assert "'=HYPERLINK" in lines[1] and ",=HYPERLINK" not in text  # defused
    xlsx = sales.get(f"{asked}&format=xlsx")
    assert xlsx.status_code == 200 and "spreadsheetml" in xlsx.headers["content-type"]
    sheet = load_workbook(io.BytesIO(xlsx.content)).active
    cells = [[c.value for c in row] for row in sheet.iter_rows()]
    assert cells[0][0] == "Invoice" and cells[1][3].startswith("'=HYPERLINK")
    assert cells[1][5] == 2000 and sheet.max_row == 2  # taxable amount is a number
    count = sql(api_database, "select count(*) from erp.audit_log where action = 'report.run' and "
                "entity_id = 'sales_register'")[0]
    assert count >= 3  # the JSON run, the CSV and the Excel file
    assert sales.get(f"{R}/sales_register?format=docx").status_code == 422


def test_bad_filters_are_refused(make_client, api_database):
    sales = Fresh(make_client, "SALES")
    assert sales.get(f"{R}/sales_register?nope=1").status_code == 422
    assert sales.get(f"{R}/sales_register?date_from=yesterday").status_code == 422
    assert sales.get(f"{R}/sales_register?customer_id=not-a-uuid").status_code == 422
    assert sales.get(f"{R}/receivables_outstanding?min_days_overdue=many").status_code == 422
    assert sales.get(f"{R}/sales_register?status=").status_code == 422
    assert sales.get(f"{R}/sales_register?limit=0").status_code == 422
    assert sales.get(f"{R}/sales_register?limit=99999").status_code == 422


def test_a_report_is_a_printable_pdf_with_every_row_and_safe_text(make_client, api_database):
    from pypdf import PdfReader  # noqa: PLC0415

    w = receivables_world(make_client, api_database)
    unit = w["unit"]["unit_id"]
    invoice(make_client, w, "10")
    sql(api_database, "update erp.customer set name = %s where customer_id = %s",
        "Maruti <b>Suzuki</b> & Sons हिन्दी", w["customer"]["customer_id"])
    sales = Fresh(make_client, "SALES")
    got = sales.get(f"{R}/sales_register?unit_id={unit}&format=pdf")
    assert got.status_code == 200 and got.content.startswith(b"%PDF-")
    assert "application/pdf" in got.headers["content-type"]
    assert got.headers["content-disposition"].endswith('.pdf"')
    assert got.headers["x-content-type-options"] == "nosniff"
    pages = PdfReader(io.BytesIO(got.content)).pages
    text = "\n".join(p.extract_text() for p in pages)
    assert "Sales register" in text and "Filters: unit_id" in text and "1 row(s)" in text
    assert "Page 1 of 1" in text and "2,000.00" in text  # the taxable amount, formatted
    assert "Maruti <b>Suzuki</b> & Sons" in text  # shown as text, never read as markup
    assert "?" in text  # the characters the standard fonts cannot draw
    empty = sales.get(f"{R}/sales_register?unit_id={unit}&status=cancelled&format=pdf")
    assert empty.status_code == 200
    assert "No rows" in PdfReader(io.BytesIO(empty.content)).pages[0].extract_text()


def test_a_long_pdf_repeats_its_header_and_numbers_its_pages(make_client, api_database):
    from pypdf import PdfReader  # noqa: PLC0415

    w = receivables_world(make_client, api_database)
    unit = w["unit"]["unit_id"]
    invoice(make_client, w, "10")
    sql(api_database, "insert into erp.customer_invoice(tenant_id, unit_id, customer_id, "
        "dispatch_id, invoice_no, issue_date, due_date, taxable_amount, tax_amount, status) "
        "select tenant_id, unit_id, customer_id, dispatch_id, 'BULK-' || g, issue_date, due_date, "
        "100, 18, 'CANCELLED' from erp.customer_invoice, generate_series(1, 120) g where "
        "unit_id = %s and status = 'ISSUED' limit 120", unit)
    got = Fresh(make_client, "SALES").get(f"{R}/sales_register?unit_id={unit}&format=pdf")
    assert got.status_code == 200
    pages = PdfReader(io.BytesIO(got.content)).pages
    assert len(pages) >= 2
    first, last = pages[0].extract_text(), pages[-1].extract_text()
    assert "Invoice" in last and f"Page {len(pages)} of {len(pages)}" in last  # header repeats
    assert "Page 1 of" in first
