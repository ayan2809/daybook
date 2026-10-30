import { NavLink } from "react-router";
import { CalendarDays, MessageCircleQuestion, Settings, Sun } from "lucide-react";

const TABS = [
  { to: "/today", label: "Today", Icon: Sun },
  { to: "/timeline", label: "Timeline", Icon: CalendarDays },
  { to: "/ask", label: "Ask", Icon: MessageCircleQuestion },
  { to: "/settings", label: "Settings", Icon: Settings },
];

export function TabBar() {
  return (
    <nav className="tabbar" aria-label="Main">
      {TABS.map(({ to, label, Icon }) => (
        <NavLink key={to} to={to} className={({ isActive }) => (isActive ? "tab active" : "tab")}>
          <Icon size={22} aria-hidden="true" />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
