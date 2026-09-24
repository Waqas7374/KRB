import { z } from "zod";

/**
 * Mirrors core/security.py `validate_password_strength`: length-first
 * (NIST 800-63B), 12–128 characters, no character-class rules. The server
 * also rejects common passwords and ones containing the username; those
 * come back as a 422 and are shown on the field.
 */
export const newPasswordField = z
  .string()
  .min(12, "At least 12 characters")
  .max(128, "At most 128 characters");

export const newPasswordSchema = z
  .object({ new_password: newPasswordField, confirm: z.string() })
  .refine((v) => v.new_password === v.confirm, {
    path: ["confirm"],
    message: "The two passwords do not match",
  });
