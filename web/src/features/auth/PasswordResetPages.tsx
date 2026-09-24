import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { applyServerErrors, describeError } from "@/lib/errors";
import type { MessageResponse } from "@/types/models";

import { AuthLayout, FormAlert } from "./auth-layout";
import { newPasswordSchema } from "./password-schema";

const forgotSchema = z.object({
  identifier: z.string().trim().min(3, "Enter your email or phone number"),
});

export function ForgotPasswordPage() {
  const [sent, setSent] = useState<MessageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const form = useForm<z.infer<typeof forgotSchema>>({
    resolver: zodResolver(forgotSchema),
    defaultValues: { identifier: "" },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      setSent(await api.post<MessageResponse>("/auth/password/forgot", values));
    } catch (err) {
      setError(describeError(err));
    }
  });

  return (
    <AuthLayout title="Reset your password" subtitle="We will send a single-use link.">
      {sent ? (
        <div className="flex flex-col gap-3">
          {/* The server answers identically whether or not the account
              exists, so neither does this screen. */}
          <FormAlert tone="success">{sent.message}</FormAlert>
          {sent.detail && <p className="text-sm text-fg-muted">{sent.detail}</p>}
          <Link to="/login" className="text-sm text-primary hover:underline">
            Back to sign in
          </Link>
        </div>
      ) : (
        <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-3" noValidate>
          {error && <FormAlert>{error}</FormAlert>}
          <FormField label="Email or phone" error={form.formState.errors.identifier?.message}>
            <Input autoComplete="username" autoFocus {...form.register("identifier")} />
          </FormField>
          <Button type="submit" variant="primary" loading={form.formState.isSubmitting}>
            Send reset link
          </Button>
          <Link to="/login" className="text-center text-sm text-primary hover:underline">
            Back to sign in
          </Link>
        </form>
      )}
    </AuthLayout>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const form = useForm<z.infer<typeof newPasswordSchema>>({
    resolver: zodResolver(newPasswordSchema),
    defaultValues: { new_password: "", confirm: "" },
  });

  const onSubmit = form.handleSubmit(async ({ new_password }) => {
    setError(null);
    try {
      const res = await api.post<MessageResponse>("/auth/password/reset", { token, new_password });
      setDone(res.message);
    } catch (err) {
      const rest = applyServerErrors(err, form.setError, ["new_password"]);
      if (rest.length) setError(rest.join(" "));
    }
  });

  if (!token) {
    return (
      <AuthLayout title="Reset link incomplete">
        <p className="text-sm text-fg-muted">
          This page needs the full link from the reset email. Request a new one if it has expired.
        </p>
        <Link
          to="/forgot-password"
          className="mt-3 inline-block text-sm text-primary hover:underline"
        >
          Request a new link
        </Link>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Choose a new password">
      {done ? (
        <div className="flex flex-col gap-3">
          <FormAlert tone="success">{done}</FormAlert>
          <Link to="/login" className="text-sm text-primary hover:underline">
            Sign in
          </Link>
        </div>
      ) : (
        <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-3" noValidate>
          {error && <FormAlert>{error}</FormAlert>}
          <FormField
            label="New password"
            hint="At least 12 characters."
            error={form.formState.errors.new_password?.message}
          >
            <Input
              type="password"
              autoComplete="new-password"
              autoFocus
              {...form.register("new_password")}
            />
          </FormField>
          <FormField label="Repeat new password" error={form.formState.errors.confirm?.message}>
            <Input type="password" autoComplete="new-password" {...form.register("confirm")} />
          </FormField>
          <Button type="submit" variant="primary" loading={form.formState.isSubmitting}>
            Set password
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}
