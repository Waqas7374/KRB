import { AlertTriangle } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Rendered instead of the default panel. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
  /** Name of the region that failed, shown to the user and logged. */
  label?: string;
}

interface State {
  error: Error | null;
}

/**
 * Catches render-time failures so one broken panel does not blank the whole
 * application. Mounted at the route level and around each dashboard region.
 */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`render failed in ${this.props.label ?? "application"}`, error, info);
  }

  private readonly reset = () => this.setState({ error: null });

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);

    return (
      <div
        role="alert"
        className="m-4 rounded-md border p-4"
        style={{ borderColor: "var(--danger)", background: "var(--danger-bg)" }}
      >
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" style={{ color: "var(--danger)" }} />
          <div className="min-w-0">
            <h2 className="font-medium">
              {this.props.label ? `${this.props.label} could not be displayed` : "Something broke"}
            </h2>
            <p className="mt-1 text-sm" style={{ color: "var(--fg-muted)" }}>
              {error.message}
            </p>
            <button
              type="button"
              onClick={this.reset}
              className="mt-3 rounded-md border px-3 py-1.5 text-sm font-medium"
              style={{ borderColor: "var(--border-strong)" }}
            >
              Try again
            </button>
          </div>
        </div>
      </div>
    );
  }
}
