import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, Download, Trash2 } from "lucide-react";
import { useRef, useState } from "react";

import { Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Select } from "@/components/ui/input";
import { ErrorState } from "@/components/ui/states";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { describeError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import { formatDateTime, humanize } from "@/lib/utils";
import type { AttachmentRead, AttachmentUpload, AttachmentDownload } from "@/types/models";

const ACCEPT = "image/jpeg,image/png,image/webp,application/pdf";
const MAX_BYTES = 25 * 1024 * 1024;

const size = (bytes: number) =>
  bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1048576).toFixed(1)} MB`;

/**
 * The photos and documents attached to one record: a truck's photo, a challan, a bill.
 * A file goes straight to storage on a short-lived address the API hands out, then the
 * API checks what actually landed before it counts (docs/06 §4, "Media").
 */
export function AttachmentsSection({
  entityType,
  entityId,
  canUpload,
  documentTypes = ["PHOTO", "CHALLAN", "BILL", "OTHER"],
  title = "Photos and documents",
}: {
  entityType: string;
  entityId: string;
  canUpload: boolean;
  documentTypes?: string[];
  title?: string;
}) {
  const canView = useCan("attachments.view");
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState(documentTypes[0] ?? "OTHER");
  const [removing, setRemoving] = useState<AttachmentRead | null>(null);
  const key = ["attachments", entityType, entityId];

  const list = useQuery({
    queryKey: key,
    queryFn: () =>
      api.get<AttachmentRead[]>("/attachments", {
        query: { entity_type: entityType, entity_id: entityId },
      }),
    enabled: canView,
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      if (file.size > MAX_BYTES) throw new Error("That file is larger than 25 MB.");
      const slot = await api.post<AttachmentUpload>("/attachments/presign", {
        entity_type: entityType,
        entity_id: entityId,
        file_name: file.name,
        content_type: file.type,
        size_bytes: file.size,
        document_type: kind,
      });
      const sent = await fetch(slot.upload_url, {
        method: slot.method,
        headers: { "Content-Type": file.type },
        body: file,
      });
      if (!sent.ok) throw new Error(`The file could not be stored (${sent.status}).`);
      return api.post<AttachmentRead>(`/attachments/${slot.attachment_id}/confirm`);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: key });
      toast.success("File attached.");
    },
    onError: (err) => toast.error(describeError(err)),
  });

  const open = async (a: AttachmentRead) => {
    try {
      const link = await api.get<AttachmentDownload>(`/attachments/${a.id}/download`);
      window.open(link.url, "_blank", "noopener");
    } catch (err) {
      toast.error(describeError(err));
    }
  };

  if (!canView) return null;
  const rows = (list.data ?? []).filter((a) => a.is_confirmed);

  return (
    <Section title={title}>
      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : rows.length === 0 ? (
        <p className="px-4 py-3 text-sm text-fg-muted">
          {list.isLoading ? "Loading…" : "Nothing attached yet."}
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-2 text-sm">
              <span className="min-w-0 flex-1 truncate font-medium">{a.file_name}</span>
              {a.document_type && (
                <span className="text-xs text-fg-muted">{humanize(a.document_type)}</span>
              )}
              <span className="text-xs text-fg-muted">
                {size(a.size_bytes)} · {formatDateTime(a.uploaded_at ?? a.created_at)}
              </span>
              <Button size="sm" onClick={() => void open(a)} aria-label={`Open ${a.file_name}`}>
                <Download /> Open
              </Button>
              {canUpload && (
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Remove ${a.file_name}`}
                  onClick={() => setRemoving(a)}
                >
                  <Trash2 />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canUpload && (
        <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3">
          <Select
            aria-label="What this file is"
            className="w-40"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            {documentTypes.map((t) => (
              <option key={t} value={t}>
                {humanize(t)}
              </option>
            ))}
          </Select>
          <input
            ref={input}
            type="file"
            accept={ACCEPT}
            capture="environment"
            className="sr-only"
            aria-label="Choose a file to attach"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) upload.mutate(file);
              e.target.value = "";
            }}
          />
          <Button onClick={() => input.current?.click()} loading={upload.isPending}>
            <Camera /> Add photo or file
          </Button>
          <span className="text-xs text-fg-subtle">JPEG, PNG, WebP or PDF, up to 25 MB.</span>
        </div>
      )}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Remove ${removing?.file_name ?? "file"}?`}
        description="It disappears from this record. The file itself is kept in storage as evidence."
        confirmLabel="Remove"
        destructive
        reason={{ label: "Reason", required: true, minLength: 3 }}
        onConfirm={async (reason) => {
          if (!removing) return;
          await api.post(`/attachments/${removing.id}/remove`, { reason });
          await queryClient.invalidateQueries({ queryKey: key });
          toast.success("Removed.");
        }}
      />
    </Section>
  );
}
