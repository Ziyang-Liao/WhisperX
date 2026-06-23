import { NavLink, Outlet } from "react-router-dom";

export default function Layout() {
  return (
    <div className="app-layout">
      <aside className="sidebar">
        <h1>🎬 视频字幕</h1>
        <nav>
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            视频字幕
          </NavLink>
          <NavLink to="/tasks" className={({ isActive }) => (isActive ? "active" : "")}>
            字幕任务
          </NavLink>
        </nav>
      </aside>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
