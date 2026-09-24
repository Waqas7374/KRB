import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { applyServerErrors } from "@/lib/errors";
import { toast } from "@/lib/toast";

import { AuthLayout, FormAlert } from "./auth-layout";
import { useAuthStore } from "./auth-store";
import { newPasswordField } from "./password-schema";

const schema = z
  .object({
    current_password: z.string().min(1, "Enter your current password"),
    new_password: newPasswordField,
    confirm: z.string(),
  })
  .refine((v) => v.new_password === v.confirm, {
    path: ["confirm"],
    message: "The two passwords do not match",
  })
  .refine((v) => v.new_password !== v.current_password, {
    path: ["new_password"],
    message: "Choose a password different from the current one",
  });
type Values = z.infer<typeof schema>;

/**
 * Used both voluntarily (user menu) and as a forced step when the account
 * has `must_change_password` set — seeded and admin-reset accounts. While
 * forced, the shell is not rendered and every route leads here.
 */
export function ChangePasswordPage() {
  const me = useAuthStore((s) => s.me);
  const reloadMe = useAuthStore((s) => s.reloadMe);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const [formError, setFormError] = useState<string | null>(null);
  const forced = Boolean(me?.user.must_change_password);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: "", new_password: "", confirm: "" },
  });

  const onSubmit = form.handleSubmit(async ({ current_password, new_password }) => {
    setFormError(null);
    try {
      await api.post("/auth/password/change", { current_password, new_password });
      await reloadMe();
      toast.success("Password changed. Other devices have been signed out.");
      navigate("/", { replace: true });
    } catch (err) {
      const rest = applyServerErrors(err, form.setError, ["current_password", "new_password"]);
      if (rest.length) setFormError(rest.join(" "));
    }
  });

  return (
    <AuthLayout
      title={forced ? "Set a new password" : "Change password"}
      subtitle={
        forced
          ? "Your account was set up with a temporary password. Choose your own before continuing."
          : "You will stay signed in here; other devices will be signed out."
      }
    >
      <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-3" noValidate>
        {formError && <FormAlert>{formError}</FormAlert>}
        <FormField
          label={forced ? "Temporary password" : "Current password"}
          error={form.formState.errors.current_password?.message}
        >
          <Input
            type="password"
            autoComplete="current-password"
            autoFocus
            {...form.register("current_password")}
          />
        </FormField>
        <FormField
          label="New password"
          hint="At least 12 characters. A short phrase is easier to remember than symbols."
          error={form.formState.errors.new_password?.message}
        >
          <Input type="password" autoComplete="new-password" {...form.register("new_password")} />
        </FormField>
        <FormField label="Repeat new password" error={form.formState.errors.confirm?.message}>
          <Input type="password" autoComplete="new-password" {...form.register("confirm")} />
        </FormField>
        <Button
          type="submit"
          variant="primary"
          loading={form.formState.isSubmitting}
          className="mt-1"
        >
          Change password
        </Button>
        {forced ? (
          <Button variant="link" onClick={() => void logout()} className="self-center">
            Sign out instead
          </Button>
        ) : (
          <Button variant="link" onClick={() => navigate(-1)} className="self-center">
            Cancel
          </Button>
        )}
      </form>
    </AuthLayout>
  );
}
