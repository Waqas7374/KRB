import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, Navigate, useLocation } from "react-router-dom";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { describeError } from "@/lib/errors";

import { AuthLayout, FormAlert } from "./auth-layout";
import { useAuthStore } from "./auth-store";

const schema = z.object({
  identifier: z.string().trim().min(3, "Enter your email or phone number"),
  password: z.string().min(1, "Enter your password"),
});
type Values = z.infer<typeof schema>;

function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "That email/phone and password do not match.";
    if (error.status === 423) return error.message || "The account is temporarily locked.";
  }
  return describeError(error);
}

export function LoginPage() {
  const status = useAuthStore((s) => s.status);
  const sessionExpired = useAuthStore((s) => s.sessionExpired);
  const login = useAuthStore((s) => s.login);
  const location = useLocation();
  const [error, setError] = useState<string | null>(null);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { identifier: "", password: "" },
  });

  if (status === "authenticated") {
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={from && from !== "/login" ? from : "/"} replace />;
  }

  const onSubmit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      await login(values.identifier, values.password);
    } catch (err) {
      setError(loginErrorMessage(err));
      form.setValue("password", "");
      form.setFocus("password");
    }
  });

  return (
    <AuthLayout title="Sign in" subtitle="Use your work email or mobile number.">
      <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-3" noValidate>
        {sessionExpired && !error && (
          <FormAlert>Your session ended. Sign in again to continue.</FormAlert>
        )}
        {error && <FormAlert>{error}</FormAlert>}
        <FormField label="Email or phone" error={form.formState.errors.identifier?.message}>
          <Input autoComplete="username" autoFocus {...form.register("identifier")} />
        </FormField>
        <FormField label="Password" error={form.formState.errors.password?.message}>
          <Input type="password" autoComplete="current-password" {...form.register("password")} />
        </FormField>
        <Button
          type="submit"
          variant="primary"
          loading={form.formState.isSubmitting}
          className="mt-1"
        >
          Sign in
        </Button>
        <Link to="/forgot-password" className="text-center text-sm text-primary hover:underline">
          Forgot your password?
        </Link>
      </form>
    </AuthLayout>
  );
}
