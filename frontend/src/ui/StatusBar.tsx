/**
 * StatusBar —— §4 必备状态：默认 / 保存中 / 已保存 / 失败。
 *
 * **不谎报**（I2 / D59）：`saved` 只允许在动作真的成功之后被设置 —— 组件不做乐观推断，
 * 它只负责把调用方给的状态渲染出来。所以这里的状态是从**请求结果**推出来的，
 * 不是从「用户点了按钮」推出来的（调用方负责这个纪律，见 PaperPage 的 persistState）。
 */

import type { ReactNode } from 'react';

export type StatusBarState = 'idle' | 'saving' | 'saved' | 'error';

export interface StatusBarProps {
  state: StatusBarState;
  /** 左侧的固定事实（题目数 / 页码 / 行数 / 种子 …） */
  items?: ReadonlyArray<ReactNode>;
  /** 状态短句（「已保存，预览已刷新」…）；`saving` 时也应给出可读文案而不是空白 */
  message: string;
  /** 右侧补充信息（纸面尺寸、缩放…） */
  trailing?: ReadonlyArray<ReactNode>;
  testId?: string;
}

const STATE_ICON: Record<StatusBarState, string> = {
  idle: '',
  saving: '…',
  saved: '✓',
  error: '!',
};

export function StatusBar({ state, items, message, trailing, testId }: StatusBarProps) {
  return (
    <footer className="mg-statusbar" data-state={state} data-testid={testId} role="status">
      {items?.map((item, index) => (
        <span className="mg-statusbar-item" key={index}>
          {item}
        </span>
      ))}
      <span className="mg-statusbar-message">
        {STATE_ICON[state] ? (
          <span className="mg-statusbar-icon" aria-hidden="true">
            {STATE_ICON[state]}
          </span>
        ) : null}
        {message}
      </span>
      {trailing ? (
        <span className="mg-statusbar-trailing">
          {trailing.map((item, index) => (
            <span className="mg-statusbar-item" key={index}>
              {item}
            </span>
          ))}
        </span>
      ) : null}
    </footer>
  );
}
