/** 顶层错误边界：任何渲染期异常都落到可读页面，绝不白屏。 */

import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 老师本机部署，控制台是唯一日志出口；不引入远端上报（离线可用，NFR-9）
    console.error('[mathgen] 界面渲染异常', error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="fatal-error" role="alert" data-testid="fatal-error">
          <h1>界面出现异常</h1>
          <p>这不是白屏：下面的信息可以直接贴给开发排查。</p>
          <pre>{this.state.error.message}</pre>
          <pre>{this.state.error.stack ?? '(无堆栈)'}</pre>
          <button type="button" className="btn" onClick={() => this.setState({ error: null })}>
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
