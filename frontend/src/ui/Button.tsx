/**
 * Button / IconButton —— §4 必备状态：默认 / hover / active / disabled / focus-visible。
 *
 * `focus-visible` 不在这里写样式（那是 styles.css 的事），但**必须**由组件保证：
 * 不用 `outline: none` 抹掉浏览器默认环，也不用 `tabIndex={-1}` 把自己排除在键盘之外
 * （I1：键盘全程可达）。门禁 A3 会 Tab 一遍并断言焦点环存在。
 */

import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type ButtonVariant = 'default' | 'primary' | 'danger' | 'ghost';
export type ButtonSize = 'md' | 'sm';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
}

export function Button({
  variant = 'default',
  size = 'md',
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  const classes = ['mg-btn', `mg-btn-${variant}`, size === 'sm' ? 'mg-btn-sm' : '', className ?? '']
    .filter(Boolean)
    .join(' ');
  return (
    <button
      {...rest}
      type={type}
      className={classes}
      data-variant={variant}
      data-state={rest.disabled ? 'disabled' : 'default'}
    >
      {children}
    </button>
  );
}

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** 无障碍必填（§4）：图标按钮没有可读文本，必须给 aria-label */
  'aria-label': string;
  children: ReactNode;
}

export function IconButton({ className, children, type = 'button', ...rest }: IconButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      className={['mg-icon-btn', className ?? ''].filter(Boolean).join(' ')}
      data-state={rest.disabled ? 'disabled' : 'default'}
    >
      {children}
    </button>
  );
}
