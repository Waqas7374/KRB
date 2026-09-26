import { api } from "@/lib/api";

/**
 * Fetch a file with the session's credentials and hand it to the browser as a
 * download. A plain link cannot be used: the API authenticates with a bearer
 * token, not a cookie.
 */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const blob = await api.get<Blob>(path, { asBlob: true });
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
