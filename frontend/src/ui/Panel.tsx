/**
 * Card / FloatingPanel —— §4 必备状态：默认 / 可折叠 / 收起。
 *
 * 浮动层用 `--mg-radius-float` + `--mg-shadow-float`（§4）。
 *
 * ≤2K 做两件事：
 *   - `Card`：页面内的静态分区（无阴影，只有描边），用于配置页的分维卡片；
 *   - `FloatingPanel`：**浮在画布之上**的层（工具栏 / 侧栏 / 面板），带浮动投影。
 * 两者分开是有意的：把「有投影」当成一种语义，而不是随手加 `box-shadow`——
 * 判据 A7 要断言浮动层之间不互相压住，前提是「哪些是浮动层」有明确集合。
 */

import type { ReactNode } from 'react';

export interface CardProps {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** 分组标题（`─ 版面 ─` 那种），用于面板内的小节 */
  as?: 'section' | 'div';
  testId?: string;
  className?: string;
}

export function Card({ title, actions, children, as = 'section', testId, className }: CardProps) {
  const Tag = as;
  return (
    <Tag className={['mg-card', className ?? ''].filter(Boolean).join(' ')} data-testid={testId}>
      {title || actions ? (
        <div className="mg-card-head">
          {title ? <h2 className="mg-card-title">{title}</h2> : null}
          {actions ? <div className="mg-card-actions">{actions}</div> : null}
        </div>
      ) : null}
      <div className="mg-card-body">{children}</div>
    </Tag>
  );
}

export interface FloatingPanelProps {
  children: ReactNode;
  ariaLabel: string;
  /** 浮动层身份：门禁 A7 按它找「可能互相压住」的层 */
  layer: string;
  /** 折叠态（`可折叠 / 收起` 两个必备状态） */
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  testId?: string;
  className?: string;
}

export function FloatingPanel({
  children,
  ariaLabel,
  layer,
  collapsed = false,
  onToggleCollapse,
  testId,
  className,
}: FloatingPanelProps) {
  return (
    <aside
      className={['mg-float', collapsed ? 'mg-float-collapsed' : '', className ?? '']
        .filter(Boolean)
        .join(' ')}
      aria-label={ariaLabel}
      data-layer={layer}
      data-state={collapsed ? 'collapsed' : onToggleCollapse ? 'collapsible' : 'default'}
      data-testid={testId}
    >
      {onToggleCollapse ? (
        <button
          type="button"
          className="mg-float-toggle"
          aria-expanded={!collapsed}
          aria-label={collapsed ? `展开${ariaLabel}` : `收起${ariaLabel}`}
          onClick={onToggleCollapse}
        >
          {collapsed ? '›' : '‹'}
        </button>
      ) : null}
      {collapsed ? null : children}
    </aside>
  );
}
