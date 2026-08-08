import React from "react";

interface State {
  error: Error | null;
}

// per-route boundary: a failed page never takes the shell down with it
export class ErrorBoundary extends React.Component<React.PropsWithChildren, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div role="alert" style={{ padding: 24 }}>
          <h2>Something went wrong on this page</h2>
          <p className="num" style={{ color: "var(--text-dim)" }}>
            {this.state.error.message}
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}
