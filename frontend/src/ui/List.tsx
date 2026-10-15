/**
 * List（题目列表）—— §4 必备状态：空 / 有数据 / 斑马纹 / hover 行。
 * 约定：每行操作**不超过 2 个**；序号右对齐等宽（§4）。
 *
 * `empty` 状态必须能给出下一步动作，所以 `emptyAction` 是可传的（§4 的空态要求）。
 */

import type { ReactNode } from 'react';

export interface ListProps {
  /** 列表为空时渲染的内容（必须含下一步动作，别只写「暂无数据」） */
  emptyAction?: ReactNode;
  isEmpty: boolean;
  children: ReactNode;
  ariaLabel: string;
  testId?: string;
}

export function List({ emptyAction, isEmpty, children, ariaLabel, testId }: ListProps) {
  return (
    <ul
      className="mg-list"
      data-state={isEmpty ? 'empty' : 'data'}
      aria-label={ariaLabel}
      data-testid={testId}
    >
      {isEmpty ? <li className="mg-list-empty">{emptyAction}</li> : children}
    </ul>
  );
}
