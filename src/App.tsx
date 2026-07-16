import {
  BookOpen,
  Bot,
  CheckCircle2,
  Database,
  FileText,
  Loader2,
  Mic,
  MicOff,
  RefreshCw,
  Send,
  ShieldCheck,
  Square,
  ThumbsDown,
  ThumbsUp,
  User,
  Volume2,
  X
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./components/ui/table";
import { Tabs } from "./components/ui/tabs";
import { Textarea } from "./components/ui/textarea";
import {
  Citation,
  CitationDetail,
  ConversationHistoryMessage,
  DocumentRecord,
  getCitation,
  getConversationMessages,
  getDocuments,
  reindexDocuments,
  submitFeedback,
  streamChat
} from "./lib/api";
import { cn } from "./lib/utils";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  persistedIndex?: number;
  streaming?: boolean;
};

type SpeechRecognitionConstructor = new () => SpeechRecognition;

type SpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
};

type SpeechRecognitionEvent = {
  results: ArrayLike<ArrayLike<{ transcript: string }>>;
};

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

const starterPrompts = [
  "How many days of PTO do employees get?",
  "What should I do if I lose my laptop?",
  "What is the deductible on the Bronze plan?"
];
const CONVERSATION_STORAGE_KEY = "meridian_conversation_id";
const freshMessages = (): ChatMessage[] => [
  {
    id: uid(),
    role: "assistant",
    content: "Meridian knowledge assistant is ready.",
    citations: []
  }
];

function uid() {
  return crypto.randomUUID();
}

function renderText(text: string) {
  const blocks = text.split("\n").filter((line) => line.trim().length > 0);
  return blocks.map((block, blockIndex) => {
    const parts = block.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
    return (
      <p className="message-markdown mb-2 last:mb-0" key={`${block}-${blockIndex}`}>
        {parts.map((part, index) => {
          if (part.startsWith("**") && part.endsWith("**")) {
            return <strong key={index}>{part.slice(2, -2)}</strong>;
          }
          if (part.startsWith("`") && part.endsWith("`")) {
            return (
              <code className="rounded bg-slate-100 px-1.5 py-0.5 text-[0.9em] text-[#182127]" key={index}>
                {part.slice(1, -1)}
              </code>
            );
          }
          return <span key={index}>{part}</span>;
        })}
      </p>
    );
  });
}

function App() {
  const [view, setView] = useState("chat");
  const [messages, setMessages] = useState<ChatMessage[]>(freshMessages);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [nextMessageIndex, setNextMessageIndex] = useState(0);
  const [selectedCitation, setSelectedCitation] = useState<CitationDetail | null>(null);
  const [citationLoading, setCitationLoading] = useState(false);
  const [citationError, setCitationError] = useState<string | null>(null);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [adminError, setAdminError] = useState<string | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const [recognizing, setRecognizing] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [feedbackNotes, setFeedbackNotes] = useState<Record<string, string>>({});
  const [feedbackStatus, setFeedbackStatus] = useState<Record<string, "up" | "down" | "error">>({});
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const speechRecognitionSupported = useMemo(
    () => typeof window !== "undefined" && Boolean(window.SpeechRecognition || window.webkitSpeechRecognition),
    []
  );
  const speechSynthesisSupported = useMemo(
    () => typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window,
    []
  );

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    void restoreConversation();
  }, []);

  useEffect(() => {
    if (view === "admin") void loadDocuments();
  }, [view]);

  async function loadDocuments() {
    setDocumentsLoading(true);
    setAdminError(null);
    try {
      const payload = await getDocuments();
      setDocuments(payload.documents);
    } catch (error) {
      setAdminError(error instanceof Error ? error.message : "Unable to load documents.");
    } finally {
      setDocumentsLoading(false);
    }
  }

  function setActiveConversation(nextConversationId: string | null) {
    setConversationId(nextConversationId);
    if (nextConversationId) {
      localStorage.setItem(CONVERSATION_STORAGE_KEY, nextConversationId);
    } else {
      localStorage.removeItem(CONVERSATION_STORAGE_KEY);
    }
  }

  function hydrateMessages(history: ConversationHistoryMessage[]) {
    if (history.length === 0) {
      setMessages(freshMessages());
      setNextMessageIndex(0);
      return;
    }
    setMessages(
      history.map((message, index) => ({
        id: uid(),
        role: message.role,
        content: message.content,
        citations: [],
        persistedIndex: index
      }))
    );
    setNextMessageIndex(history.length);
  }

  async function restoreConversation() {
    const storedConversationId = localStorage.getItem(CONVERSATION_STORAGE_KEY);
    if (!storedConversationId) {
      setHistoryLoading(false);
      return;
    }

    try {
      const payload = await getConversationMessages(storedConversationId);
      setActiveConversation(payload.conversation_id);
      hydrateMessages(payload.messages);
    } catch (error) {
      localStorage.removeItem(CONVERSATION_STORAGE_KEY);
      setConversationId(null);
      setMessages(freshMessages());
    } finally {
      setHistoryLoading(false);
    }
  }

  function startNewConversation() {
    window.speechSynthesis?.cancel();
    recognitionRef.current?.stop();
    setRecognizing(false);
    setActiveConversation(null);
    setSelectedCitation(null);
    setCitationError(null);
    setInput("");
    setMessages(freshMessages());
    setNextMessageIndex(0);
    setFeedbackNotes({});
    setFeedbackStatus({});
  }

  async function handleReindex() {
    setReindexing(true);
    setAdminError(null);
    try {
      const payload = await reindexDocuments();
      setDocuments(payload.documents);
    } catch (error) {
      setAdminError(error instanceof Error ? error.message : "Re-index failed.");
    } finally {
      setReindexing(false);
    }
  }

  async function sendMessage(text = input) {
    const trimmed = text.trim();
    if (!trimmed || isSending) return;

    const userMessage: ChatMessage = { id: uid(), role: "user", content: trimmed, citations: [], persistedIndex: nextMessageIndex };
    const assistantId = uid();
    const assistantPersistedIndex = nextMessageIndex + 1;
    setInput("");
    setIsSending(true);
    setMessages((current) => [
      ...current,
      userMessage,
      { id: assistantId, role: "assistant", content: "", citations: [], persistedIndex: assistantPersistedIndex, streaming: true }
    ]);

    try {
      await streamChat(trimmed, conversationId, {
        onMetadata: (payload) => {
          setActiveConversation(payload.conversation_id);
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId ? { ...message, citations: payload.citations } : message
            )
          );
        },
        onToken: (token) => {
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId ? { ...message, content: `${message.content}${token}` } : message
            )
          );
        },
        onDone: (payload) => {
          setActiveConversation(payload.conversation_id);
          setNextMessageIndex((current) => Math.max(current, assistantPersistedIndex + 1));
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? { ...message, content: payload.response, citations: payload.citations, streaming: false }
                : message
            )
          );
        }
      });
    } catch (error) {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content: error instanceof Error ? error.message : "Request failed.",
                citations: [],
                streaming: false
              }
            : message
        )
      );
      setNextMessageIndex((current) => Math.max(current, nextMessageIndex));
    } finally {
      setIsSending(false);
    }
  }

  async function sendFeedback(message: ChatMessage, rating: "up" | "down") {
    if (!conversationId || message.persistedIndex === undefined) return;
    try {
      await submitFeedback({
        conversation_id: conversationId,
        message_index: message.persistedIndex,
        rating,
        note: feedbackNotes[message.id]?.trim() || undefined
      });
      setFeedbackStatus((current) => ({ ...current, [message.id]: rating }));
    } catch (error) {
      setFeedbackStatus((current) => ({ ...current, [message.id]: "error" }));
    }
  }

  async function openCitation(citation: Citation) {
    setCitationLoading(true);
    setSelectedCitation(null);
    setCitationError(null);
    try {
      const detail = await getCitation(citation.chunk_id);
      setSelectedCitation(detail);
    } catch (error) {
      setCitationError(error instanceof Error ? error.message : "Unable to load source passage.");
    } finally {
      setCitationLoading(false);
    }
  }

  function startVoiceInput() {
    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Recognition) return;

    if (recognizing) {
      recognitionRef.current?.stop();
      setRecognizing(false);
      return;
    }

    const recognition = new Recognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";
    recognition.onresult = (event) => {
      const transcript = event.results[0]?.[0]?.transcript ?? "";
      if (transcript) setInput((current) => (current ? `${current} ${transcript}` : transcript));
    };
    recognition.onerror = () => {
      setVoiceError("Voice input is unavailable in this browser session.");
      setRecognizing(false);
    };
    recognition.onend = () => setRecognizing(false);
    recognitionRef.current = recognition;
    setVoiceError(null);
    setRecognizing(true);
    recognition.start();
  }

  function speak(text: string) {
    if (!speechSynthesisSupported) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 0.98;
    utterance.pitch = 1;
    window.speechSynthesis.speak(utterance);
  }

  return (
    <main className="min-h-screen bg-[#f7f8fa]">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-4 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
          <div className="flex items-center gap-3">
            <div className="brand-gradient flex h-11 w-11 items-center justify-center rounded-xl text-white shadow-sm">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-[#182127]">Meridian Knowledge Assistant</h1>
              <p className="text-sm text-slate-500">DataFactZ Week 1 RAG workspace</p>
            </div>
          </div>
          <Tabs
            value={view}
            onValueChange={setView}
            items={[
              { value: "chat", label: "Chat", icon: <Bot className="h-4 w-4" /> },
              { value: "admin", label: "Admin", icon: <Database className="h-4 w-4" /> }
            ]}
          />
        </div>
      </header>

      {view === "chat" ? (
        <section className="mx-auto grid max-w-7xl gap-5 px-4 py-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_380px] lg:px-8">
          <Card className="flex min-h-[calc(100vh-140px)] flex-col overflow-hidden">
            <CardHeader className="border-b border-slate-100">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <CardTitle>Chat</CardTitle>
                  <Badge>{conversationId ? "Conversation active" : "New conversation"}</Badge>
                </div>
                <Button disabled={historyLoading || isSending} onClick={startNewConversation} size="sm" type="button" variant="outline">
                  New conversation
                </Button>
              </div>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-4 overflow-hidden p-4">
              <div className="flex-1 overflow-y-auto pr-1">
                {historyLoading ? (
                  <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Loading conversation
                  </div>
                ) : (
                <div className="space-y-4">
                  {messages.map((message) => (
                    <div
                      className={cn(
                        "flex gap-3",
                        message.role === "user" ? "justify-end" : "justify-start"
                      )}
                      key={message.id}
                    >
                      {message.role === "assistant" && (
                        <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#182127] text-white">
                          <Bot className="h-4 w-4" />
                        </div>
                      )}
                      <div
                        className={cn(
                          "max-w-[780px] rounded-xl px-4 py-3 text-sm leading-6 shadow-sm",
                          message.role === "user"
                            ? "brand-gradient text-white"
                            : "border border-slate-200 bg-white text-slate-800"
                        )}
                      >
                        {message.content ? renderText(message.content) : <Loader2 className="h-4 w-4 animate-spin" />}
                        {message.streaming && message.content && <span className="ml-1 inline-block h-4 w-1 animate-pulse bg-[#FC7900]" />}
                        {message.role === "assistant" && message.citations.length > 0 && (
                          <div className="mt-3 flex flex-wrap gap-2 border-t border-slate-100 pt-3">
                            {message.citations.map((citation) => (
                              <button
                                className="inline-flex items-center gap-1.5 rounded-full border border-[#FC7900]/25 bg-[#F4AD0B]/10 px-2.5 py-1 text-xs font-semibold text-[#182127] transition hover:bg-[#F4AD0B]/20"
                                key={citation.chunk_id}
                                onClick={() => void openCitation(citation)}
                                type="button"
                              >
                                <FileText className="h-3.5 w-3.5" />
                                {citation.document} · {citation.section}
                              </button>
                            ))}
                          </div>
                        )}
                        {message.role === "assistant" && speechSynthesisSupported && message.content && (
                          <div className="mt-3 flex justify-end">
                            <Button
                              aria-label="Read response aloud"
                              onClick={() => speak(message.content)}
                              size="sm"
                              type="button"
                              variant="ghost"
                            >
                              <Volume2 className="h-4 w-4" />
                              Read
                            </Button>
                          </div>
                        )}
                        {message.role === "assistant" && conversationId && message.persistedIndex !== undefined && !message.streaming && (
                          <div className="mt-3 border-t border-slate-100 pt-3">
                            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                              <input
                                aria-label="Feedback note"
                                className="h-8 min-w-0 flex-1 rounded-lg border border-slate-200 px-2.5 text-xs outline-none focus:border-[#FC7900] focus:ring-2 focus:ring-[#FC7900]/20"
                                onChange={(event) =>
                                  setFeedbackNotes((current) => ({ ...current, [message.id]: event.target.value }))
                                }
                                placeholder="Optional feedback note"
                                value={feedbackNotes[message.id] ?? ""}
                              />
                              <div className="flex gap-1">
                                <Button
                                  aria-label="Thumbs up"
                                  onClick={() => void sendFeedback(message, "up")}
                                  size="sm"
                                  type="button"
                                  variant={feedbackStatus[message.id] === "up" ? "brand" : "outline"}
                                >
                                  <ThumbsUp className="h-4 w-4" />
                                </Button>
                                <Button
                                  aria-label="Thumbs down"
                                  onClick={() => void sendFeedback(message, "down")}
                                  size="sm"
                                  type="button"
                                  variant={feedbackStatus[message.id] === "down" ? "destructive" : "outline"}
                                >
                                  <ThumbsDown className="h-4 w-4" />
                                </Button>
                              </div>
                            </div>
                            {feedbackStatus[message.id] && (
                              <p className="mt-1 text-xs text-slate-500">
                                {feedbackStatus[message.id] === "error" ? "Feedback was not saved." : "Feedback saved."}
                              </p>
                            )}
                          </div>
                        )}
                      </div>
                      {message.role === "user" && (
                        <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-[#182127]">
                          <User className="h-4 w-4" />
                        </div>
                      )}
                    </div>
                  ))}
                  <div ref={scrollRef} />
                </div>
                )}
              </div>

              {!historyLoading && <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <div className="mb-3 flex flex-wrap gap-2">
                  {starterPrompts.map((prompt) => (
                    <Button
                      disabled={isSending}
                      key={prompt}
                      onClick={() => void sendMessage(prompt)}
                      size="sm"
                      type="button"
                      variant="outline"
                    >
                      {prompt}
                    </Button>
                  ))}
                </div>
                <div className="flex gap-2">
                  <Textarea
                    aria-label="Message"
                    disabled={isSending}
                    onChange={(event) => setInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        void sendMessage();
                      }
                    }}
                    placeholder="Ask about Meridian policies, benefits, or procedures"
                    value={input}
                  />
                  <div className="flex flex-col gap-2">
                    {speechRecognitionSupported && (
                      <Button
                        aria-label={recognizing ? "Stop voice input" : "Start voice input"}
                        onClick={startVoiceInput}
                        size="icon"
                        type="button"
                        variant={recognizing ? "destructive" : "outline"}
                      >
                        {recognizing ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
                      </Button>
                    )}
                    <Button aria-label="Send" disabled={isSending || input.trim().length === 0} onClick={() => void sendMessage()} size="icon" type="button" variant="brand">
                      {isSending ? <Square className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                    </Button>
                  </div>
                </div>
                {voiceError && <p className="mt-2 text-xs text-[#E3434A]">{voiceError}</p>}
              </div>}
            </CardContent>
          </Card>

          <CitationPanel
            detail={selectedCitation}
            error={citationError}
            loading={citationLoading}
            onClose={() => {
              setSelectedCitation(null);
              setCitationError(null);
            }}
          />
        </section>
      ) : (
        <AdminPage
          documents={documents}
          error={adminError}
          loading={documentsLoading}
          onRefresh={() => void loadDocuments()}
          onReindex={() => void handleReindex()}
          reindexing={reindexing}
        />
      )}
    </main>
  );
}

type CitationPanelProps = {
  detail: CitationDetail | null;
  error: string | null;
  loading: boolean;
  onClose: () => void;
};

function CitationPanel({ detail, error, loading, onClose }: CitationPanelProps) {
  return (
    <aside className="min-h-[calc(100vh-140px)]">
      <Card className="sticky top-5 h-full max-h-[calc(100vh-140px)] overflow-hidden">
        <CardHeader className="border-b border-slate-100">
          <div className="flex items-center justify-between gap-3">
            <CardTitle>Source Passage</CardTitle>
            {(detail || error) && (
              <Button aria-label="Close source panel" onClick={onClose} size="icon" type="button" variant="ghost">
                <X className="h-4 w-4" />
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="h-[calc(100%-73px)] overflow-y-auto p-5">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading source
            </div>
          )}
          {!loading && error && (
            <div className="rounded-xl border border-red-100 bg-red-50 p-4 text-sm leading-6 text-[#E3434A]">
              {error}
            </div>
          )}
          {!loading && !detail && !error && (
            <div className="flex h-full flex-col items-center justify-center text-center text-sm text-slate-500">
              <BookOpen className="mb-3 h-8 w-8 text-slate-300" />
              Select a citation to inspect the source text.
            </div>
          )}
          {!loading && detail && (
            <div className="space-y-4">
              <div>
                <h2 className="text-base font-semibold text-[#182127]">{detail.document}</h2>
                <p className="mt-1 text-sm text-slate-500">
                  {detail.section} · chunk {detail.chunk_index}
                </p>
              </div>
              <pre className="whitespace-pre-wrap rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm leading-6 text-slate-700">
                {detail.text}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>
    </aside>
  );
}

type AdminPageProps = {
  documents: DocumentRecord[];
  error: string | null;
  loading: boolean;
  onRefresh: () => void;
  onReindex: () => void;
  reindexing: boolean;
};

function AdminPage({ documents, error, loading, onRefresh, onReindex, reindexing }: AdminPageProps) {
  const totalChunks = documents.reduce((total, document) => total + document.chunk_count, 0);

  return (
    <section className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
      <Card>
        <CardHeader className="border-b border-slate-100">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <CardTitle>Indexed Documents</CardTitle>
              <p className="mt-1 text-sm text-slate-500">
                {documents.length} documents · {totalChunks} chunks
              </p>
            </div>
            <div className="flex gap-2">
              <Button disabled={loading || reindexing} onClick={onRefresh} type="button" variant="outline">
                <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
                Refresh
              </Button>
              <Button disabled={reindexing} onClick={onReindex} type="button" variant="brand">
                {reindexing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
                Re-index
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {error && <div className="border-b border-red-100 bg-red-50 px-5 py-3 text-sm text-[#E3434A]">{error}</div>}
          {reindexing && (
            <div className="flex items-center gap-2 border-b border-[#FC7900]/20 bg-[#F4AD0B]/10 px-5 py-3 text-sm text-[#182127]">
              <Loader2 className="h-4 w-4 animate-spin" />
              Re-indexing corpus and refreshing Azure AI Search
            </div>
          )}
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Filename</TableHead>
                  <TableHead>Format</TableHead>
                  <TableHead className="text-right">Chunks</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {documents.map((document) => (
                  <TableRow key={document.id}>
                    <TableCell className="font-medium text-[#182127]">{document.filename}</TableCell>
                    <TableCell>
                      <Badge>{document.format.toUpperCase()}</Badge>
                    </TableCell>
                    <TableCell className="text-right font-semibold">{document.chunk_count}</TableCell>
                  </TableRow>
                ))}
                {!loading && documents.length === 0 && (
                  <TableRow>
                    <TableCell className="py-8 text-center text-slate-500" colSpan={3}>
                      No indexed documents found.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
          {!error && documents.length > 0 && (
            <div className="flex items-center gap-2 border-t border-slate-100 px-5 py-3 text-sm text-slate-500">
              <CheckCircle2 className="h-4 w-4 text-[#FC7900]" />
              Index metadata loaded from SQLite.
            </div>
          )}
        </CardContent>
      </Card>
    </section>
  );
}

export default App;
