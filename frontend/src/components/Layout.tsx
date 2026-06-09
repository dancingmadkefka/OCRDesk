import { NavLink, Outlet, useLocation } from "react-router-dom";
import RestartServerButton from "./RestartServerButton";

export default function Layout() {
  const { pathname } = useLocation();
  const cinema = pathname.startsWith("/workspace");

  return (
    <div className={`app-shell${cinema ? " cinema" : ""}`}>
      {!cinema && (
        <aside className="sidebar">
          <NavLink to="/" className="sidebar-brand" title="OCRDesk">
            <span className="sidebar-logo">OCR</span>
            <span className="sidebar-tagline">Desk</span>
          </NavLink>

          <nav className="sidebar-nav">
            <NavLink to="/" end className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}>
              <span className="sidebar-link-icon" aria-hidden>
                ⊞
              </span>
              <span>Corpus</span>
            </NavLink>
            <NavLink to="/settings" className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}>
              <span className="sidebar-link-icon" aria-hidden>
                ⚙
              </span>
              <span>Settings</span>
            </NavLink>
          </nav>

          <div className="sidebar-footer">
            <RestartServerButton />
          </div>
        </aside>
      )}

      <div className="app-stage">
        <Outlet />
      </div>
    </div>
  );
}
