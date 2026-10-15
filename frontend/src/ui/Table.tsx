/**
 * Table（卷库）—— §4 必备状态：空 / 有数据 / loading / 分页。
 *
 * 约定：表头与数字列对齐；**空态给下一步动作**而不是只写「暂无数据」（§4）。
 *
 * 这里不做分页逻辑本身（分页属于页面），只负责「表在 loading / empty / data 三态下长什么样」，
 * 并把状态挂到 `data-state` 上供门禁断言。空态与 loading 互斥且都必须有可读文案 ——
 * 门禁 A2 会断言这两个状态文案存在，防的是「白屏」。
 */

import type { ReactNode } from 'react';

export interface TableColumn {
  key: string;
  header: string;
  /** 数字列（右对齐、等宽） */
  numeric?: boolean;
}

export interface TableProps {
  columns: ReadonlyArray<TableColumn>;
  /** 每行一个 key；单元格由 row 渲染 */
  rows: ReadonlyArray<{ key: string | number; cells: ReactNode[]; testAttrs?: Record<string, string> }>;
  isLoading?: boolean;
  loadingText?: string;
  /** 空态内容：必须含下一步动作 */
  emptyAction: ReactNode;
  ariaLabel: string;
  testId?: string;
  /**
   * 给 `<tbody>` 挂 testid。
   *
   * 为什么需要它：既有的卷库门禁按 `[data-testid="library-rows"] tr` 数行 /
   * 读每行的 `data-paper-id`。那个 testid 原本就挂在 `<tbody>` 上 —— 语义是
   * 「数据行的容器」。把它挪到外层 wrapper 会改变含义（wrapper 在空态/加载态下也在），
   * 所以这里支持显式指定，让选择器语义不变（任务卡：换选择器必须说明为什么含义不变）。
   */
  bodyTestId?: string;
  /**
   * 错误态内容（与 `emptyAction` **分开**，不共用槽位）。
   *
   * 为什么必须分开（Lead 裁决，M3.5）：把 `ErrorState` 塞进 `emptyAction` 时，
   * wrapper 的 `data-state` 会是 `empty` —— 一个**错误**场景对外宣称「没有数据」，
   * 这是语义层的小谎报，和 D59（界面谎报已保存）、D65（`?page=2` 静默拿到第一页）
   * 是同一条原则：**能说真话的地方不要含糊**。它还会误导下一个读代码的人：
   * 看到 `empty` 会以为确实没数据，而不是「请求失败了」。
   *
   * 给了 `errorAction` 就表示调用方**已经**知道出错了，此时 `data-state="error"`。
   */
  errorAction?: ReactNode;
}

export function Table({
  columns,
  rows,
  isLoading = false,
  loadingText = '正在读取…',
  emptyAction,
  errorAction,
  ariaLabel,
  testId,
  bodyTestId,
}: TableProps) {
  // 四态互斥且各有明确语义：loading / error / empty / data。
  // 顺序有讲究：错误优先于空 —— 请求失败时「表里没有行」是**结果**，不是「库里没有数据」。
  const state = isLoading
    ? 'loading'
    : errorAction
      ? 'error'
      : rows.length === 0
        ? 'empty'
        : 'data';
  return (
    <div className="mg-table-wrap" data-state={state} data-testid={testId}>
      {isLoading ? (
        <p className="mg-table-loading" role="status">
          {loadingText}
        </p>
      ) : errorAction ? (
        <div className="mg-table-error">{errorAction}</div>
      ) : rows.length === 0 ? (
        <div className="mg-table-empty">{emptyAction}</div>
      ) : (
        <table className="mg-table" aria-label={ariaLabel}>
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={column.numeric ? 'mg-cell-numeric' : undefined}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody data-testid={bodyTestId}>
            {rows.map((row) => (
              <tr key={row.key} {...(row.testAttrs ?? {})}>
                {row.cells.map((cell, index) => (
                  <td
                    key={columns[index]?.key ?? index}
                    className={columns[index]?.numeric ? 'mg-cell-numeric' : undefined}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
