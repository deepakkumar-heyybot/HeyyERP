import { useLayoutEffect, useRef } from "react";
import { Link } from "react-router-dom";

export function ManufacturingTabs({ active }: { active: "compounds" | "batches" | "equipment" | "work-orders" | "lots" | "shifts" | "performance" }) {
  const navRef = useRef<HTMLElement>(null);
  const activeRef = useRef<HTMLAnchorElement>(null);
  useLayoutEffect(() => {
    const revealActive = () => {
      const nav = navRef.current, link = activeRef.current;
      if (!nav || !link) return;
      const container = nav.getBoundingClientRect(), target = link.getBoundingClientRect();
      if (target.right > container.right) nav.scrollLeft += target.right - container.right;
      else if (target.left < container.left) nav.scrollLeft += target.left - container.left;
    };
    revealActive();
    window.addEventListener("resize", revealActive);
    return () => window.removeEventListener("resize", revealActive);
  }, [active]);
  return <nav ref={navRef} className="organization-tabs" aria-label="Manufacturing pages">
    <Link ref={active === "compounds" ? activeRef : undefined} className={active === "compounds" ? "active" : ""} aria-current={active === "compounds" ? "page" : undefined} to="/app/manufacturing/compounds">Compounds</Link>
    <Link ref={active === "batches" ? activeRef : undefined} className={active === "batches" ? "active" : ""} aria-current={active === "batches" ? "page" : undefined} to="/app/manufacturing/batches">Batches</Link>
    <Link ref={active === "equipment" ? activeRef : undefined} className={active === "equipment" ? "active" : ""} aria-current={active === "equipment" ? "page" : undefined} to="/app/manufacturing/equipment">Equipment</Link>
    <Link ref={active === "work-orders" ? activeRef : undefined} className={active === "work-orders" ? "active" : ""} aria-current={active === "work-orders" ? "page" : undefined} to="/app/manufacturing/work-orders">Work orders</Link>
    <Link ref={active === "lots" ? activeRef : undefined} className={active === "lots" ? "active" : ""} aria-current={active === "lots" ? "page" : undefined} to="/app/manufacturing/lots">Production lots</Link>
    <Link ref={active === "shifts" ? activeRef : undefined} className={active === "shifts" ? "active" : ""} aria-current={active === "shifts" ? "page" : undefined} to="/app/manufacturing/shift-entries">Shift entries</Link>
    <Link ref={active === "performance" ? activeRef : undefined} className={active === "performance" ? "active" : ""} aria-current={active === "performance" ? "page" : undefined} to="/app/manufacturing/performance">WIP &amp; OEE</Link>
  </nav>;
}
