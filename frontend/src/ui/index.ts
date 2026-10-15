/**
 * 老师端 UI 组件层 —— M3.5 结构轮（task-15）。
 *
 * 为什么要有这一层：M2 交付时「按钮 / 开关 / 输入框 / 表格」都是**逐页手写 class**，
 * 于是同一个控件在不同页面状态不一致（有的有 focus 环、有的没有；有的 disabled 只是变浅灰
 * 却仍可点）。把控件收进这一层之后：
 *   ① 状态齐不齐可以**被机器检查**（`data-state` + 样式表存在性，见判据 A2）；
 *   ② 令牌只有一处引用（判据 A1：本目录不得出现颜色字面量）；
 *   ③ 视觉轮只需要改 `styles.css` 与 `tokens.css`，不必碰页面。
 *
 * 约定：
 *   - 样式全部走 `var(--mg-*)`，本目录**不得**出现颜色字面量（A1，扫描器见
 *     `scripts/verify-tokens.mjs`；具体写法见该脚本头注释，这里不重复写以免被自己的扫描命中）；
 *   - 每个组件的 DOM 上带 `data-state`，供门禁断言「状态真的切换了」；
 *   - 不用任何 UI 框架（D18），不用外部资源（NFR-9）。
 */

export { Button, IconButton, type ButtonProps, type IconButtonProps } from './Button';
export { Input, NumberInput, type InputProps, type NumberInputProps } from './Input';
export { Checkbox, type CheckboxProps } from './Checkbox';
export { Switch, type SwitchProps } from './Switch';
export { Slider, type SliderProps } from './Slider';
export { SegmentedControl, type SegmentedControlProps } from './SegmentedControl';
export { List, type ListProps } from './List';
export { Table, type TableColumn, type TableProps } from './Table';
export { Card, FloatingPanel, type CardProps, type FloatingPanelProps } from './Panel';
export { Toolbar, ToolbarDivider, type ToolbarProps } from './Toolbar';
export { StatusBar, type StatusBarProps, type StatusBarState } from './StatusBar';
export { Badge, Chip, type BadgeProps, type ChipProps } from './Badge';
export { EmptyState, ErrorState, type EmptyStateProps, type ErrorStateProps } from './State';
export { Toast, ToastStack, type ToastProps } from './Toast';
