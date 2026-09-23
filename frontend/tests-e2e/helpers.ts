import { type Locator, type Page, expect } from "@playwright/test";

export function uniqueEmail(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.floor(Math.random() * 10_000)}@example.com`;
}

// Several of these AI/RAG-gated pages call their LLM/embedding provider
// synchronously during server-side rendering (explain on load; review/
// architecture/agent/search/debug once their "run" query param is set) -
// the page's own navigation doesn't resolve until that call does. A
// "not configured" gap fails fast, well within Playwright's normal 5s
// default; a real Groq/local-embedding round trip can legitimately take
// longer. This shared, generous bound is for exactly that wait - still a
// bounded, deterministic Playwright auto-wait (not a fixed sleep), just
// sized for a live network call instead of an in-process error.
export const AI_ROUND_TRIP_TIMEOUT_MS = 20_000;

// Several AI/RAG-gated pages behave correctly in two different ways
// depending on which LLM/embedding provider the *backend* actually has
// configured: CI never sets one, so every call fails with a graceful
// "not configured" error; a developer's local .env may have a real,
// working provider (Groq/local fastembed), so the same call succeeds
// with a real answer instead. Playwright runs in its own process and
// can't read the backend's .env to know which case applies up front, so
// this waits for whichever of the two mutually-exclusive, backend-driven
// outcomes actually renders - both are legitimate, so this isn't a
// flaky/arbitrary wait, just Playwright's normal auto-waiting resolving
// on whichever locator's condition the backend already decided.
export async function expectGracefulOrRealResult(
  page: Page,
  gapPattern: RegExp,
  successLocator: Locator
): Promise<"gap" | "success"> {
  const gapLocator = page.getByText(gapPattern);
  await expect(gapLocator.or(successLocator).first()).toBeVisible({
    timeout: AI_ROUND_TRIP_TIMEOUT_MS,
  });
  if (await gapLocator.isVisible()) {
    return "gap";
  }
  await expect(successLocator).toBeVisible();
  return "success";
}
