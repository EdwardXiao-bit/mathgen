/** 老师端外壳：顶部导航 + 后端可用性横幅 + 路由出口（契约 §7 的路由在 App.tsx 声明）。 */

import { NavLink, Outlet } from 'react-router-dom';

import { useBackendHealth } from './useBackendHealth';

export function TeacherShell() {
  const health = useBackendHealth();

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand">
          <span className="app-brand-mark">mathgen</span>
          <span className="app-brand-sub">老师端 · M2</span>
        </div>
        <nav className="app-nav">
          <NavLink to="/teacher" end className={({ isActive }) => (isActive ? 'nav-on' : '')}>
            出题配置
          </NavLink>
          <NavLink to="/teacher/library" className={({ isActive }) => (isActive ? 'nav-on' : '')}>
            卷库
          </NavLink>
        </nav>
        <div className="app-health" data-backend-status={health.status}>
          <span className={`health-dot health-${health.status}`} aria-hidden="true" />
          {health.status === 'online' ? '后端已连接' : null}
          {health.status === 'checking' ? '正在连接后端…' : null}
          {health.status === 'offline' ? '后端未连接' : null}
        </div>
      </header>

      {health.message ? (
        <div
          className={`offline-banner ${health.status === 'offline' ? 'offline-banner-warn' : 'offline-banner-info'}`}
          role="status"
          data-testid="backend-banner"
        >
          <strong>{health.status === 'offline' ? '后端未就绪' : '后端返回错误'}</strong>
          <span>{health.message}</span>
        </div>
      ) : null}

      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
