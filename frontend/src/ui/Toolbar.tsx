/**
 * Toolbar —— §4 必备状态：默认 / 分组分隔。
 *
 * 约定：**浮于画布之上，不得压住纸面**（几何断言 A7）。
 * 实现上它作为画布列的**第一行占位**（flex column），而不是绝对定位盖上去 ——
 * 这是 D62 实测缺陷的直接对策：样张初版用 `position:absolute` 把工具栏压在纸面顶部。
 * 用文档流占位后，「不压住纸面」是布局自然结果，而不是靠调 `top` 数值凑出来的。
 */

import type { ReactNode } from 'react';

export interface ToolbarProps {
  children: ReactNode;
  ariaLabel: string;
  /** 浮动层身份（A7 几何断言用） */
  layer?: string;
  testId?: string;
}

/** 工具栏内的分组分隔竖线（§4「分组分隔」状态） */
export function ToolbarDivider() {
  return <span className="mg-toolbar-divider" aria-hidden="true" />;
}

export function Toolbar({ children, ariaLabel, layer = 'toolbar', testId }: ToolbarProps) {
  return (
    <div
      className="mg-toolbar"
      role="toolbar"
      aria-label={ariaLabel}
      data-layer={layer}
      data-state="default"
      data-testid={testId}
    >
      {children}
    </div>
  );
}
