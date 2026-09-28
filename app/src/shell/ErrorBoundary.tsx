import { Component, type ErrorInfo, type ReactNode } from "react";

import { reportClientError } from "@/api/client";
import { ErrorState } from "@/ui/feedback";

/**
 * Catches a render error in one screen so the rest of the app keeps working.
 * Without it React unmounts everything and the window goes black. ``resetKey``
 * (the current route) clears the error when the user navigates elsewhere.
 */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    reportClientError(`render: ${error.message} ${info.componentStack?.split("\n").slice(0, 3).join(" ") ?? ""}`);
  }

  componentDidUpdate(previous: { resetKey?: string }) {
    if (this.state.error && previous.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <ErrorState
          title="Esta pantalla tuvo un error"
          impact="El motor y las protecciones siguen funcionando; solo falló la vista."
          detail={this.state.error.message}
          onRetry={() => this.setState({ error: null })}
        />
      </div>
    );
  }
}
