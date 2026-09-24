import { isRouteErrorResponse, Link, useRouteError } from "react-router-dom";

/** Rendered when a route throws while loading — typically a code-split chunk
 *  that no longer exists because a new version was deployed meanwhile. */
export function RouteError() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : "Unknown error";
  return (
    <div role="alert" className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-xl font-semibold">This screen could not be loaded</h1>
      <p className="mt-2 text-sm text-fg-muted">{message}</p>
      <p className="mt-2 text-sm text-fg-muted">
        If the application was just updated, reloading the page usually fixes this.
      </p>
      <button
        type="button"
        onClick={() => window.location.reload()}
        className="mt-4 text-sm text-primary hover:underline"
      >
        Reload
      </button>
    </div>
  );
}

export function NotFound() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-xl font-semibold">Page not found</h1>
      <p className="mt-2 text-sm text-fg-muted">
        The address you opened does not match any screen in this application.
      </p>
      <Link to="/" className="mt-4 inline-block text-sm text-primary hover:underline">
        Go to the home page
      </Link>
    </div>
  );
}
