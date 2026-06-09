import { NavLink, Outlet } from "react-router-dom";
import RestartServerButton from "./RestartServerButton";

export default function Layout() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <NavLink to="/" className="brand">
          <span className="brand-mark">◫</span>
          <span className="brand-text">OCR Benchmark</span>
        </NavLink>
        <nav className="topnav">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Dashboard
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => (isActive ? "active" : "")}>
            Settings
          </NavLink>
        </nav>
        <div className="topbar-actions">
          <RestartServerButton />
        </div>
      </header>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
