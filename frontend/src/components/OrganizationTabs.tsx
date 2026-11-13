import { NavLink } from "react-router-dom";

export function OrganizationTabs({ active }: { active: "plants" | "departments" | "stores" }) {
  return (
    <nav className="organization-tabs" aria-label="Organization master sections">
      <NavLink className={active === "plants" ? "active" : ""} to="/app/master-data/organization">Plants</NavLink>
      <NavLink className={active === "departments" ? "active" : ""} to="/app/master-data/organization/departments">Departments</NavLink>
      <NavLink className={active === "stores" ? "active" : ""} to="/app/master-data/organization/stores">Stores</NavLink>
    </nav>
  );
}
