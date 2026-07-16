export type Citation = {
  document: string;
  section: string;
  chunk_id: string;
};

export type ChatResponse = {
  response: string;
  citations: Citation[];
  conversation_id: string;
};

export type DocumentRecord = {
  id: number;
  filename: string;
  format: string;
  chunk_count: number;
};

export type CitationDetail = {
  chunk_id: string;
  document: string;
  section: string;
  chunk_index: number;
  text: string;
};

export type ConversationHistoryMessage = {
  role: "user" | "assistant";
  content: string;
  created_at: number;
};

export type FeedbackRating = "up" | "down";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const API_KEY = import.meta.env.VITE_API_KEY ?? "";

function headers() {
  return {
    "Content-Type": "application/json",
    "X-API-Key": API_KEY
  };
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function getDocuments() {
  const response = await fetch(`${API_BASE_URL}/api/v1/documents`, {
    headers: { "X-API-Key": API_KEY }
  });
  return parseJson<{ documents: DocumentRecord[] }>(response);
}

export async function reindexDocuments() {
  const response = await fetch(`${API_BASE_URL}/api/v1/documents/reindex`, {
    method: "POST",
    headers: { "X-API-Key": API_KEY }
  });
  return parseJson<{ documents: DocumentRecord[]; total_documents: number; total_chunks: number }>(response);
}

export async function getCitation(chunkId: string) {
  const response = await fetch(`${API_BASE_URL}/api/v1/citations/${chunkId}`, {
    headers: { "X-API-Key": API_KEY }
  });
  return parseJson<CitationDetail>(response);
}

export async function getConversationMessages(conversationId: string) {
  const response = await fetch(`${API_BASE_URL}/api/v1/conversations/${conversationId}/messages`, {
    headers: { "X-API-Key": API_KEY }
  });
  return parseJson<{ conversation_id: string; messages: ConversationHistoryMessage[] }>(response);
}

export async function submitFeedback(payload: {
  conversation_id: string;
  message_index: number;
  rating: FeedbackRating;
  note?: string;
}) {
  const response = await fetch(`${API_BASE_URL}/api/v1/feedback`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(payload)
  });
  return parseJson<{ id: string; conversation_id: string; rating: FeedbackRating; saved: boolean }>(response);
}

type StreamHandlers = {
  onMetadata?: (payload: { conversation_id: string; citations: Citation[] }) => void;
  onToken?: (token: string) => void;
  onDone?: (payload: ChatResponse) => void;
};

export async function streamChat(
  message: string,
  conversationId: string | null,
  handlers: StreamHandlers
) {
  const response = await fetch(`${API_BASE_URL}/api/v1/chat`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ message, conversation_id: conversationId, stream: true })
  });

  if (!response.ok || !response.body) {
    const body = await response.text();
    throw new Error(body || `Chat request failed with ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function handleEvent(rawEvent: string) {
    const lines = rawEvent.split("\n");
    const eventLine = lines.find((line) => line.startsWith("event:"));
    const dataLines = lines.filter((line) => line.startsWith("data:"));
    if (!eventLine || dataLines.length === 0) return;
    const event = eventLine.replace("event:", "").trim();
    const dataText = dataLines.map((line) => line.replace("data:", "").trimStart()).join("\n");

    if (event === "token") {
      handlers.onToken?.(dataText);
      return;
    }

    const payload = JSON.parse(dataText);
    if (event === "metadata") handlers.onMetadata?.(payload);
    if (event === "done") handlers.onDone?.(payload);
  }

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) handleEvent(event);
  }

  if (buffer.trim()) handleEvent(buffer);
}
