import { Link } from "react-router-dom";

export function InventoryTabs({ active = "grns", canIssue = false }: { active?: "grns" | "stock" | "issues" | "history" | "reservations" | "transfers" | "counts" | "reports"; canIssue?: boolean }) {
  return <nav className="organization-tabs" aria-label="Inventory pages">
    <Link className={active === "grns" ? "active" : ""} aria-current={active === "grns" ? "page" : undefined} to="/app/inventory/grns">Goods receipts</Link>
    <Link className={active === "stock" ? "active" : ""} aria-current={active === "stock" ? "page" : undefined} to="/app/inventory/stock">Stock</Link>
    {canIssue && <Link className={active === "issues" ? "active" : ""} aria-current={active === "issues" ? "page" : undefined} to="/app/inventory/issues/new">Issue planner</Link>}
    <Link className={active === "history" ? "active" : ""} aria-current={active === "history" ? "page" : undefined} to="/app/inventory/issues">Issue history</Link>
    <Link className={active === "reservations" ? "active" : ""} aria-current={active === "reservations" ? "page" : undefined} to="/app/inventory/reservations">Reservations</Link>
    <Link className={active === "transfers" ? "active" : ""} aria-current={active === "transfers" ? "page" : undefined} to="/app/inventory/transfers">Transfers</Link>
    <Link className={active === "counts" ? "active" : ""} aria-current={active === "counts" ? "page" : undefined} to="/app/inventory/counts">Stock counts</Link>
    <Link className={active === "reports" ? "active" : ""} aria-current={active === "reports" ? "page" : undefined} to="/app/inventory/reports">Inventory reports</Link>
  </nav>;
}
