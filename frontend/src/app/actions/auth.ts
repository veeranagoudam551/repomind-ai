"use server";

import { redirect } from "next/navigation";
import { describeApiError, loginUser, registerUser } from "@/lib/api";
import { createSession, deleteSession } from "@/lib/session";

export type AuthFormState = { error: string } | undefined;

export async function login(
  _prevState: AuthFormState,
  formData: FormData
): Promise<AuthFormState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");

  if (!email || !password) {
    return { error: "Email and password are required." };
  }

  try {
    const { access_token } = await loginUser({ email, password });
    await createSession(access_token);
  } catch (err) {
    return { error: describeApiError(err) };
  }

  redirect("/dashboard");
}

export async function registerAndLogin(
  _prevState: AuthFormState,
  formData: FormData
): Promise<AuthFormState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const fullName = String(formData.get("full_name") ?? "").trim();

  if (!email || !password) {
    return { error: "Email and password are required." };
  }
  if (password.length < 8) {
    return { error: "Password must be at least 8 characters." };
  }

  try {
    await registerUser({ email, password, full_name: fullName || undefined });
    const { access_token } = await loginUser({ email, password });
    await createSession(access_token);
  } catch (err) {
    return { error: describeApiError(err) };
  }

  redirect("/dashboard");
}

export async function logout(): Promise<void> {
  await deleteSession();
  redirect("/login");
}
