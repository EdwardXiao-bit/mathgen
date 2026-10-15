/**
 * 学生端的小组件（页面外壳 / 按钮 / 提示条 / 加载态）。
 *
 * 为什么学生端不直接用 `src/ui/**`：契约 §8 D-5 规定学生端与老师端**不得共享页面组件**，
 * 只共享 `src/shared/`；而且老师端的控件尺寸是按桌面鼠标设计的（36px 高、12px 字），
 * 拿到手机上点不中、输入时还会被 iOS 自动放大。这里按手机重新做一遍。
 */
import type { ReactNode } from 'react';

/** 页面外壳：负责安全区留白，保证刘海屏 / 圆角屏上内容不被裁掉。 */
export function Screen({ children }: { children: ReactNode }) {
  return <div className="st-screen">{children}</div>;
}

/** 顶栏：左边是页面标题，右边可以放计时之类的实时信息。 */
export function TopBar({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <header className="st-topbar">
      <h1 className="st-topbar-title">{title}</h1>
      {right ? <div className="st-topbar-right">{right}</div> : null}
    </header>
  );
}

export function Button({
  children,
  onClick,
  variant = 'primary',
  disabled = false,
  type = 'button',
  block = true,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'primary' | 'ghost' | 'quiet';
  disabled?: boolean;
  type?: 'button' | 'submit';
  block?: boolean;
}) {
  return (
    <button
      type={type}
      className={`st-btn st-btn--${variant}${block ? ' st-btn--block' : ''}`}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

/** 提示条：`tone` 只影响颜色，文案一律是「发生了什么 + 怎么办」。 */
export function Note({
  tone = 'info',
  title,
  hint,
}: {
  tone?: 'info' | 'error' | 'success';
  title: string;
  hint?: string;
}) {
  return (
    <div className={`st-note st-note--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <p className="st-note-title">{title}</p>
      {hint ? <p className="st-note-hint">{hint}</p> : null}
    </div>
  );
}

export function Loading({ text }: { text: string }) {
  return (
    <div className="st-loading" role="status">
      <span className="st-spinner" aria-hidden="true" />
      <p>{text}</p>
    </div>
  );
}

/** 卡片：白底 + 一级描边，是学生端唯一的容器样式（避免页面长出一堆相似外观）。 */
export function Card({ children }: { children: ReactNode }) {
  return <section className="st-card">{children}</section>;
}
