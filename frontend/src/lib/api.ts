// Server-side client for the RepoMind AI FastAPI backend. Only call this
// from Server Components and Server Actions — never from the browser
// bundle, since it's the only thing that ever holds the session token.

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function extractErrorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string") {
      return data.detail;
    }
  } catch {
    // Not JSON — fall through to the generic message below.
  }
  return `Request failed with status ${response.status}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

export type User = {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  created_at: string;
};

export type RepositoryStatus = "pending" | "cloning" | "processing" | "completed" | "failed";

export type Repository = {
  id: string;
  github_url: string;
  name: string;
  description: string | null;
  default_branch: string | null;
  status: RepositoryStatus;
  error_message: string | null;
  file_count: number;
  total_size_bytes: number;
  created_at: string;
  updated_at: string;
};

export type AuthToken = {
  access_token: string;
  token_type: string;
};

export function registerUser(input: {
  email: string;
  password: string;
  full_name?: string;
}): Promise<User> {
  return request<User>("/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function loginUser(input: { email: string; password: string }): Promise<AuthToken> {
  return request<AuthToken>("/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getCurrentUser(token: string): Promise<User> {
  return request<User>("/auth/me", { headers: authHeaders(token) });
}

export function listRepositories(token: string): Promise<Repository[]> {
  return request<Repository[]>("/repositories", { headers: authHeaders(token) });
}

export function createRepository(token: string, githubUrl: string): Promise<Repository> {
  return request<Repository>("/repositories", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ github_url: githubUrl }),
  });
}

export function deleteRepository(token: string, id: string): Promise<void> {
  return request<void>(`/repositories/${id}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}
