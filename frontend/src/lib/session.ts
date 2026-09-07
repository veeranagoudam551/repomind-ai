import { cookies } from "next/headers";
import { SESSION_COOKIE_NAME } from "@/lib/constants";

// The backend already issues a signed JWT (see backend/app/core/security.py),
// so the cookie just carries that token as-is — no re-encryption needed. We
// only decode (never verify) it here, to line the cookie's expiry up with
// the token's own `exp` claim; the backend remains the source of truth and
// re-verifies the token on every request.
function decodeJwtExpiry(token: string): Date | undefined {
  try {
    const payload = token.split(".")[1];
    const json = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    if (typeof json.exp === "number") {
      return new Date(json.exp * 1000);
    }
  } catch {
    // Malformed token — fall back to a session-only cookie.
  }
  return undefined;
}

export async function createSession(token: string): Promise<void> {
  const cookieStore = await cookies();
  cookieStore.set(SESSION_COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires: decodeJwtExpiry(token),
  });
}

export async function getSessionToken(): Promise<string | undefined> {
  const cookieStore = await cookies();
  return cookieStore.get(SESSION_COOKIE_NAME)?.value;
}

export async function deleteSession(): Promise<void> {
  const cookieStore = await cookies();
  cookieStore.delete(SESSION_COOKIE_NAME);
}
