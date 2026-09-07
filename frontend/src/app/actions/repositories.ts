"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { ApiError, createRepository, deleteRepository, reindexRepository } from "@/lib/api";
import { getSessionToken } from "@/lib/session";

export type RepositoryFormState = { error: string; successAt?: undefined } | { successAt: number; error?: undefined } | undefined;

export async function addRepository(
  _prevState: RepositoryFormState,
  formData: FormData
): Promise<RepositoryFormState> {
  const token = await getSessionToken();
  if (!token) {
    return { error: "You must be logged in." };
  }

  const githubUrl = String(formData.get("github_url") ?? "").trim();
  if (!githubUrl) {
    return { error: "Enter a GitHub repository URL." };
  }

  try {
    await createRepository(token, githubUrl);
  } catch (err) {
    return { error: err instanceof ApiError ? err.message : "Something went wrong. Please try again." };
  }

  revalidatePath("/dashboard");
  return { successAt: Date.now() };
}

export async function removeRepository(id: string): Promise<void> {
  const token = await getSessionToken();
  if (!token) return;

  await deleteRepository(token, id);
  revalidatePath("/dashboard");
}

export async function deleteRepositoryAndRedirect(id: string): Promise<void> {
  const token = await getSessionToken();
  if (!token) {
    redirect("/login");
  }

  await deleteRepository(token, id);
  revalidatePath("/dashboard");
  redirect("/dashboard");
}

export async function reindexRepositoryAction(id: string): Promise<void> {
  const token = await getSessionToken();
  if (!token) return;

  try {
    await reindexRepository(token, id);
  } catch {
    // e.g. a 409 if ingestion was already in progress — the revalidate
    // below refreshes the page to reflect whatever the true state is.
  }

  revalidatePath(`/dashboard/${id}`);
  revalidatePath("/dashboard");
}
