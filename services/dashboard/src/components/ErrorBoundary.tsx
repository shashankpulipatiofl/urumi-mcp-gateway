import { Component, ErrorInfo, ReactNode } from "react";

// Catches rendering errors anywhere below it. Instead of a blank white page the user
// sees what went wrong, and the error is logged to the browser console.
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Dashboard crashed:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="crash">
        <h2>Something went wrong on this page</h2>
        <pre>{String(this.state.error.message || this.state.error)}</pre>
        <button className="btn btn-primary" onClick={() => this.setState({ error: null })}>
          Try again
        </button>{" "}
        <button className="btn" onClick={() => (window.location.href = "/")}>
          Back to overview
        </button>
      </div>
    );
  }
}
