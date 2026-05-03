"use client";

import React from "react";
import { MetricsProvider } from "./MetricsProvider";

class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error?: Error }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("Global error boundary caught:", error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          padding: 40,
          color: "#ff5555",
          background: "#282a36",
          fontFamily: "JetBrains Mono, monospace",
          minHeight: "100vh",
        }}>
          <h2 style={{ margin: "0 0 16px" }}>[错误] 页面渲染出错</h2>
          <p style={{ color: "#6272a4", margin: "0 0 24px" }}>
            请刷新页面重试。若问题持续，请检查控制台日志。
          </p>
          <pre style={{
            background: "#191a21",
            padding: 16,
            borderRadius: 6,
            fontSize: 12,
            overflow: "auto",
          }}>
            {this.state.error?.message}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

export function ClientProviders({ children }: { children: React.ReactNode }) {
  return (
    <ErrorBoundary>
      <MetricsProvider>{children}</MetricsProvider>
    </ErrorBoundary>
  );
}
