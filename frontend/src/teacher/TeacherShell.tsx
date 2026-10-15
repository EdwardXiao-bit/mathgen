/**
 * 老师端外壳 —— C1「工作台」骨架（D62）。
 *
 * 骨架：顶栏 44px（品牌 / 导航 / 后端连接状态）+ 主体。
 * **底部状态条 34px 由卷页自己渲染**（C1 状态条要显示题目数、页码、行数、种子、保存状态，
 * 这些只有卷页知道）。外壳不渲染它，是为了避免出现两条状态条。
 *
 * 后端可用性：顶栏的圆点 + 一条横幅。横幅文案按 I3 给「发生了什么 + 怎么办」，
 * 不是丢一个 `Error` 出来。
 */

import { NavLink, Outlet } from 'react-router-dom';

import { useBackendHealth } from './useBackendHealth';

export function TeacherShell() {
  const health = useBackendHealth();

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand">
          <span className="app-brand-mark">mathgen</span>
          <span className="app-brand-sub">老师端</span>
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
