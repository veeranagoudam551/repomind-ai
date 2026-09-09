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

export type RepositoryFile = {
  id: string;
  file_path: string;
  language: string | null;
  size_bytes: number;
  content_hash: string | null;
  created_at: string;
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

export function getRepository(token: string, id: string): Promise<Repository> {
  return request<Repository>(`/repositories/${id}`, { headers: authHeaders(token) });
}

export function listRepositoryFiles(token: string, id: string): Promise<RepositoryFile[]> {
  return request<RepositoryFile[]>(`/repositories/${id}/files`, { headers: authHeaders(token) });
}

export function reindexRepository(token: string, id: string): Promise<Repository> {
  return request<Repository>(`/repositories/${id}/reindex`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export type SearchResult = {
  code_chunk_id: string;
  file_path: string;
  content: string;
  start_line: number | null;
  end_line: number | null;
  score: number;
};

export function searchRepository(
  token: string,
  id: string,
  query: string,
  limit = 10
): Promise<SearchResult[]> {
  return request<SearchResult[]>(`/repositories/${id}/search`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ query, limit }),
  });
}

export type Conversation = {
  id: string;
  repository_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
};

export type MessageRole = "user" | "assistant" | "system";

export type MessageSource = {
  code_chunk_id: string;
  file_path: string;
  start_line: number | null;
  end_line: number | null;
};

export type ChatMessage = {
  id: string;
  role: MessageRole;
  content: string;
  sources: MessageSource[];
  created_at: string;
};

export function listConversations(token: string, repositoryId: string): Promise<Conversation[]> {
  return request<Conversation[]>(`/repositories/${repositoryId}/conversations`, {
    headers: authHeaders(token),
  });
}

export function createConversation(token: string, repositoryId: string): Promise<Conversation> {
  return request<Conversation>(`/repositories/${repositoryId}/conversations`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ title: null }),
  });
}

export function listMessages(token: string, conversationId: string): Promise<ChatMessage[]> {
  return request<ChatMessage[]>(`/conversations/${conversationId}/messages`, {
    headers: authHeaders(token),
  });
}

export function sendMessage(
  token: string,
  conversationId: string,
  content: string
): Promise<ChatMessage> {
  return request<ChatMessage>(`/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ content }),
  });
}
