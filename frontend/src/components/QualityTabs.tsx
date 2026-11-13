import { useLayoutEffect, useRef } from "react";
import { Link } from "react-router-dom";

const tabs = [
  ["queue", "/app/quality", "QA queue"], ["plans", "/app/quality/plans", "Inspection plans"],
  ["inspections", "/app/quality/inspections", "Inspections"], ["holds", "/app/quality/holds", "Quality holds"],
  ["ncr", "/app/quality/ncrs", "NCR / 8D"], ["instruments", "/app/quality/instruments", "Instruments"],
] as const;

export function QualityTabs({ active }: { active: typeof tabs[number][0] }) {
  const navRef = useRef<HTMLElement>(null), activeRef = useRef<HTMLAnchorElement>(null);
  useLayoutEffect(() => { const link = activeRef.current; link?.scrollIntoView({ block: "nearest", inline: "nearest" }); }, [active]);
  return <nav ref={navRef} className="organization-tabs" aria-label="Quality pages">{tabs.map(([id, to, label]) => <Link key={id} ref={id === active ? activeRef : undefined} className={id === active ? "active" : ""} aria-current={id === active ? "page" : undefined} to={to}>{label}</Link>)}</nav>;
}
