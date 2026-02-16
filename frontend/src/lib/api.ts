const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export interface Citation {
  url: string;
  title: string | null;
  fetched_at: string | null;
  quote: string;
}

export interface FreshnessInfo {
  strategy: string;
  as_of: string | null;
}

export interface DebugInfo {
  intent: string | null;
  live_fetch_used: boolean;
  retrieval_top_k: number;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  confidence: number;
  freshness: FreshnessInfo;
  notes: string[];
  debug: DebugInfo;
}

export interface ChatRequest {
  query: string;
  user_context?: { term?: string; major?: string };
  rmp_excerpt?: string;
}

export async function sendChat(request: ChatRequest): Promise<ChatResponse> {
  const resp = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API error ${resp.status}: ${text}`);
  }

  return resp.json();
}

export async function checkHealth(): Promise<boolean> {
  try {
    const resp = await fetch(`${API_BASE}/health`);
    return resp.ok;
  } catch {
    return false;
  }
}
