import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Textarea } from "@/components/ui/input";
import { describeError } from "@/lib/errors";

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  /** Names the action ("Suspend Shree Stone"), never "OK" — docs/08 §2. */
  confirmLabel: string;
  destructive?: boolean;
  /** When set, a reason textarea is shown; `required` makes it mandatory. */
  reason?: { label: string; required?: boolean; placeholder?: string };
  onConfirm: (reason: string) => Promise<unknown>;
  children?: ReactNode;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  destructive,
  reason,
  onConfirm,
  children,
}: ConfirmDialogProps) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reasonMissing = Boolean(reason?.required) && text.trim().length === 0;

  const close = (next: boolean) => {
    if (pending) return;
    if (!next) {
      setText("");
      setError(null);
    }
    onOpenChange(next);
  };

  const submit = async () => {
    setPending(true);
    setError(null);
    try {
      await onConfirm(text.trim());
      setText("");
      onOpenChange(false);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={title}
      description={description}
      footer={
        <>
          <Button onClick={() => close(false)} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant={destructive ? "danger" : "primary"}
            onClick={() => void submit()}
            loading={pending}
            disabled={reasonMissing}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {children}
        {reason && (
          <FormField label={reason.label} required={reason.required}>
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={reason.placeholder}
              autoFocus
            />
          </FormField>
        )}
        {error && (
          <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
