# Meridian RAG Chatbot — Defense Prep Reference
### Function-by-function walkthrough + Q&A flashcards for Friday's demo

*How to use this: skim the function tables to refresh what each piece does. Use the Q&A section as actual flashcards — cover the answer, read the question, say the answer out loud.*

---

## PART 1 — FUNCTION-BY-FUNCTION WALKTHROUGH

### `ingest.py` — offline ingestion pipeline

| Function | What it does |
|---|---|
| `token_counter()` | Returns a token-counting function using `tiktoken` (cl100k_base encoding) if available, else a word-count-based approximation (`words × 1.3`). |
| `normalize_space(text)` | Collapses 3+ consecutive newlines down to 2 — cleans up messy whitespace from parsed documents. |
| `clean_title(title)` | Strips leading Markdown `#` characters from a heading; falls back to "Untitled Section" if empty. |
| `is_numbered_clause(line)` | Regex check: does this line look like `1.` or `3.2` etc. — used to detect real policy clause structure. |
| `looks_like_pdf_heading(line)` | Heuristic: is this line short enough (4–120 chars), not a long sentence ending in a period, and matches an all-caps or numbered-section pattern? |
| `is_noisy_pdf_title(line)` | **The PDF citation-noise fix.** Rejects a candidate heading if it's empty, pure numbers/punctuation (page numbers), fewer than 4 words with no clause structure, or a short all-caps fragment (like a person's name in a header). This is what stops "DAWN MOTOVIDLAK" from becoming a section title. |
| `add_section(...)` | Appends a parsed section (title + text + optional page number) to the running list. |
| `parse_docx(path)` | Walks DOCX paragraphs, uses heading styles + numbered-clause detection to split into sections. |
| `parse_pdf(path)` | Walks PDF text per page, uses `looks_like_pdf_heading` + `is_noisy_pdf_title` to decide real headings vs. `Page N` fallback. |
| `parse_markdown(path)` | Parses on `#`/`##` heading syntax directly. |
| `parse_document(path)` | Dispatches to the right parser based on file extension. |
| `split_section_into_units(section)` | Breaks a section's text into paragraph-level units for the chunker to work with. |
| `chunk_sections(...)` | **The chunking algorithm.** For each section, accumulates units until hitting the 400–600 token target, then cuts a chunk — carrying a ~15%-of-previous-chunk tail forward as overlap. Sections whose single unit exceeds the max get sub-split by paragraph. Every chunk gets a stable SHA-256 ID derived from filename + index + text prefix. |
| `required_env(name)` | Reads an env var or raises `RuntimeError` if missing — fail-fast on misconfiguration. |
| `create_search_index(...)` | PUTs the Azure AI Search index schema (fields + HNSW vector config) — idempotent, safe to re-run. |
| `embed_texts(...)` | Batches chunk text (16 at a time) through the Azure embedding deployment. |
| `upload_chunks(...)` | Batches chunk+embedding pairs (100 at a time) as `mergeOrUpload` actions to Azure AI Search. |
| `write_sqlite(...)` | Writes `documents` and `chunks` tables — full rebuild of local metadata each run. |
| `discover_documents(corpus_root)` | Walks the corpus folder tree, returns all ingestible file paths. |
| `flag_outliers(...)` | Flags documents with chunk counts far from the corpus average — a QA signal, not a hard rule (this is what surfaced the two noisy PDFs originally). |
| `hybrid_search(...)` | Runs a raw retrieval query directly against the index — used for ingestion self-verification (the sample queries printed at the end of a run). |
| `print_verification(...)` | Prints the end-of-run summary: doc/chunk counts, outliers, sample retrieval results. |
| `run(args)` | Top-level orchestration: discover → parse → chunk → embed → upload → write SQLite → verify. |
| `build_parser()` | CLI argument parsing (env file path, etc.). |

### `app/config.py` — settings

| Function | What it does |
|---|---|
| `required_env(name)` | Same fail-fast pattern as ingest.py — missing required setting crashes on startup, not silently at request time. |
| `Settings` (dataclass) | Immutable settings object — every Azure endpoint/key, deployment names, DB path, the relevance-gate threshold (default `0.028`), and the semantic-ranking flag (default `False`). |
| `get_settings()` | Builds a `Settings` instance from environment variables, called once at app startup. |

### `app/azure_clients.py` — all outbound Azure calls

| Function | What it does |
|---|---|
| `headers(api_key)` | Builds the `api-key` + `Content-Type` header dict used on every Azure request. |
| `post_with_retry(...)` | POST with exponential backoff (up to 5 attempts, capped at 20s) on 429/500/502/503/504 — the resilience layer for every Azure call. |
| `embed(settings, texts)` | Calls the embedding deployment, returns vectors sorted back into input order (Azure doesn't guarantee response order matches request order). |
| `hybrid_retrieve(...)` | **The core retrieval function.** Embeds the query, sends a combined `search` (keyword) + `vectorQueries` (vector) request to Azure AI Search, optionally with `queryType: semantic`. Then applies the **context-assembly fix**: the top-ranked document may contribute up to 2 chunks, every other document capped at 1, total capped at `top` (5). |
| `chat_completion(...)` | Non-streaming chat completion call — used by `answer_chat` (non-SSE path) and the evaluation/comparison scripts. |
| `stream_chat_completion(...)` | Same, but as a generator yielding tokens as they arrive via SSE from Azure — used by the streaming chat endpoint. |

### `app/rag.py` — core RAG logic

| Function | What it does |
|---|---|
| `build_context(chunks)` | Wraps retrieved chunks in `<retrieved_context><chunk ...>` XML-ish tags with document/section/chunk_id metadata — this structure is what lets the model's `<used_chunks>` output be mapped back to real citations. |
| `build_messages(...)` | Assembles the full message list: system prompt + a user turn containing context + conversation history + the actual question, all clearly delimited. |
| `citations_from_chunks(chunks)` | Converts a list of `RetrievedChunk` into `Citation` schema objects — used only as a fallback/full list before filtering. |
| `filter_citations_from_response(...)` | **The citation-precision fix.** Parses the `<used_chunks>1,3</used_chunks>` marker out of the model's response text, keeps only citations matching those indexes, and strips the marker from what the user sees. Falls back to all citations if no marker is found (defensive, not silent failure). |
| `expand_retrieval_query(message)` | The curated synonym layer — appends related retrieval terms to the query text when it matches known paraphrase patterns (e.g. "computer" + "stolen" → adds "lost stolen laptop device report IT..."). |
| `normalize_terms(text)` | Lowercases, strips stopwords, does light stemming (drops trailing "s") — the term-extraction step feeding the relevance gate. |
| `expanded_terms(text)` | `normalize_terms` output, then expanded again via the `DOMAIN_HINTS` dictionary (e.g. "bronze" → also implies "cigna", "deductible", "medical", "plan"). |
| `retrieval_is_relevant(...)` | **The relevance gate.** Checks (1) top chunk's vector score ≥ threshold, (2) query doesn't contain an explicit out-of-domain term (gmail, netflix, etc.), (3) lexical/domain-term overlap between query and top-3 retrieved chunks meets a ratio-or-count bar. All three must pass. |
| `is_refusal_text(response_text)` | Checks whether the model's own response text matches the canonical refusal string — used to strip citations if the model refused despite chunks passing the gate. |
| `answer_chat(...)` | Non-streaming end-to-end handler: expand query → retrieve → gate check → (if pass) build messages → call LLM → filter citations → persist → return. Used by the non-SSE `/api/v1/chat` path and by `evaluate.py`. |

### `app/database.py` — SQLite access layer

| Function | What it does |
|---|---|
| `connect(database_path)` | Opens a SQLite connection with `Row` factory (dict-like row access) and foreign keys enabled. |
| `init_chat_tables(database_path)` | Creates `conversations`, `messages`, `feedback` tables if they don't exist — called at app startup, idempotent. |
| `ensure_conversation(...)` | Inserts a new conversation row, or updates `updated_at` if the ID already exists (upsert pattern). |
| `add_message(...)` | Inserts a user or assistant message, bumps the parent conversation's `updated_at`. |
| `get_history(database_path, conversation_id)` | Returns all messages for a conversation, ordered chronologically. |
| `conversation_exists(...)` | Simple existence check — used to return a clean 404 rather than a confusing empty result. |
| `get_message_by_id(...)` / `get_message_by_index(...)` | Two ways to look up a specific message for feedback — by its own ID, or by position within a conversation's history. |
| `add_feedback(...)` | Inserts a thumbs up/down + optional note row. |
| `format_history_for_prompt(rows)` | The history-formatting design decision made concrete: last 3 turns rendered verbatim, anything older collapsed to one-line summaries (role + truncated content). |
| `list_documents(database_path)` | Joins `documents` and `chunks`, returns per-document chunk counts for the admin view. |
| `get_chunk(database_path, chunk_id)` | Fetches full text + metadata for one chunk — powers the citation click-through. |

### `app/main.py` — FastAPI routes

| Function/route | What it does |
|---|---|
| `security_headers` (middleware) | Adds CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, referrer policy to every response. |
| `require_api_key(...)` | Dependency: compares the `X-API-Key` header against the configured secret, raises 401 if it doesn't match. |
| `document_records()` | Helper: converts raw DB rows into `DocumentRecord` schema objects. |
| `GET /health` | No auth, liveness check. |
| `POST /api/v1/chat` | Branches to streaming (SSE) or non-streaming (`answer_chat`) based on the request's `stream` flag. |
| `GET /api/v1/conversations/{id}/messages` | Returns full history for a conversation, 404 if it doesn't exist — powers refresh-continuity. |
| `POST /api/v1/feedback` | Resolves the target message (by ID or by conversation+index), inserts the feedback row. |
| `sse(event, data)` | Formats a single Server-Sent Event line. |
| `stream_chat_response(...)` | The SSE version of the chat flow: same gate/retrieve/generate logic as `answer_chat`, but yields `metadata`, `token`, and `done` events as they happen instead of returning one blob. |
| `GET /api/v1/documents` | Admin: document inventory. |
| `POST /api/v1/documents/reindex` | Admin: shells out to run `ingest.py` fresh, re-reads the DB, returns updated counts. |
| `GET /api/v1/citations/{chunk_id}` | Full source passage lookup, 404 if the chunk ID doesn't exist. |
| `unknown_api(path)` (catch-all) | Any unmatched `/api/*` route returns a proper 404 JSON instead of falling through to the SPA. |
| `frontend(path)` (catch-all) | Any non-API path serves the built React app's `index.html` (client-side routing support). |

### `app/worker.py`
A one-line customization of uvicorn's Gunicorn worker class that suppresses the `Server: uvicorn` response header — the security-hardening fix for header-based fingerprinting.

---

## PART 2 — Q&A FLASHCARDS

**Q: Why structure-aware chunking instead of fixed-size?**
A: Fixed-size chunking would cut numbered policy clauses mid-sentence, breaking citation accuracy. Structure-aware chunking respects section headers and numbered clauses, targeting 400–600 tokens with 15% overlap between chunks.

**Q: Why hybrid retrieval instead of pure vector search?**
A: Pure vector search under-serves exact-term queries — e.g. a specific plan name or dollar figure. Hybrid combines keyword matching with vector similarity in one Azure AI Search request.

**Q: Walk me through the relevance gate.**
A: Three checks, all must pass: (1) the top retrieved chunk's vector score meets a threshold, (2) the query doesn't contain an explicit out-of-domain term, (3) lexical/domain-term overlap between the query and the top-3 chunks meets a bar. This was empirically tuned — a score-only gate let "stock price" queries through at measured scores above the original threshold; a lexical-only gate then caused false refusals on legitimate paraphrases.

**Q: What happens if the gate fails?**
A: The backend returns the refusal text directly — the LLM is never called. This saves cost and guarantees the refusal is consistent, not model-dependent.

**Q: What happens if the gate passes but the question still isn't really answerable?**
A: A second, independent safeguard: the system prompt instructs the model not to invent unsupported claims. This causes graceful degradation — a partial, honestly-qualified answer — rather than a false refusal or a hallucination. Confirmed on real untested paraphrases during evaluation.

**Q: How do citations work, exactly?**
A: The model is instructed to end its response with a `<used_chunks>` marker listing which retrieved chunk indexes it actually relied on. The backend parses that marker, filters the citation list down to only those chunks, and strips the marker before displaying the response. This was a real fix — originally every retrieved chunk was returned as a citation regardless of relevance.

**Q: What was the PDF citation bug, and how was it found?**
A: Two complex-layout PDFs had heading detection picking up layout noise — a person's name, page-number headers — as section titles. Found by directly querying the rebuilt SQLite database after ingestion, not by trusting the ingestion summary. Fixed with a noise-rejection heuristic that falls back to `Page N` labeling when a candidate heading looks like noise.

**Q: What was the context-assembly bug, and how was it found?**
A: `hybrid_retrieve` originally allowed only one chunk per document in the top-5 context window, to maximize source diversity. This meant that when a correct answer needed a *second* section from the same document, that chunk could never enter context — even though the document was correctly retrieved at rank 1. Found via a 20-question evaluation harness that surfaced three false refusals; diagnosed by inspecting raw retrieval scores directly, which confirmed the relevance gate was never the problem. Fixed by letting the top-ranked document contribute up to 2 chunks.

**Q: What were the measured before/after numbers for that fix?**
A: Document citation accuracy: 85% → 95%. Judge-verified correctness: 90% → 95%, on the same 20-question harness, no regression on the other cases.

**Q: Is that fix perfect?**
A: No — one case remains unresolved (a data-classification definitional clause that doesn't rank into the top document's top-2 chunks). Left as a stated limitation rather than further tuned, to avoid overfitting the retrieval strategy to one evaluation case.

**Q: Did you try semantic ranking?**
A: Yes — configured and tested against the live index, no rebuild required. It's disabled by default because it surfaced a real interaction bug: reordering can promote a chunk with a lower original hybrid score into the top position, and the relevance gate currently checks that raw hybrid score. This caused one previously-passing query to fail when semantic ranking was on. Documented as a deliberate decision pending a gate revision, not shipped broken.

**Q: Why API-key auth instead of full login/signup?**
A: It meets the brief's stated minimum bar. Real enterprise deployments integrate with the client's existing identity provider (SSO/OIDC) rather than building custom credentials — a tool-specific password is a liability to enterprise IT (no MFA, no centralized deprovisioning), not a feature. Custom login was rejected as off-pattern and unnecessary scope/risk for a single-tenant pilot.

**Q: Why Docker instead of Azure's native Python deployment?**
A: The native buildpack (Oryx) compresses build output to a randomized temp directory at every container start. A custom gunicorn startup command referencing the app package by name broke against that indirection — confirmed as `ModuleNotFoundError` across five different startup-command strategies, with direct log evidence each time, not assumption. Docker gives a fixed, predictable filesystem layout that sidesteps the problem entirely.

**Q: Why SQLite instead of a real database, if this is enterprise-grade?**
A: Appropriate for a single-tenant pilot at this scale. Stated explicitly as a scaling limit: SQLite is single-writer, so concurrent conversation writes at production volume would hit lock contention — this is one of the first things named in the "what changes at 100x load" section, ahead of Azure AI Search's own capacity limits.

**Q: What changes first at 100x load?**
A: In order: ingestion architecture (event-triggered, hash-diffed, queued — not full-rebuild), then document access control (permission-aware connectors, not a manual folder), then SQLite → Postgres/Cosmos DB, then Azure AI Search tier, then generation routing flips to cost-optimized (DeepSeek default, GPT-5.5 escalation) rather than quality-optimized.

**Q: Why GPT-5.5 as primary instead of DeepSeek V3.2?**
A: At pilot scale and low query volume, citation-faithfulness and answer quality mattered more than marginal cost savings. At production scale, the reasoning flips — generation cost compounds linearly with volume, so a cost-tiered router becomes the right architecture, with DeepSeek as default and GPT-5.5 reserved for low-confidence retrieval cases.

**Q: How is conversation history managed?**
A: Full history is always persisted to SQLite. Only the last 3 turns are sent to the model verbatim; anything older is collapsed to a one-line summary per turn. This bounds token growth on long sessions without losing the full record.

**Q: How does the app survive a page refresh?**
A: The frontend stores the active `conversation_id` in localStorage. On load, it fetches that conversation's full history from the backend and re-renders it before showing the input box. A "New conversation" button clears the stored ID to start fresh intentionally.

**Q: What's the known security limitation, and why is it acceptable?**
A: The frontend bundle contains a single shared API key, baked in at build time (a Vite constraint — env vars resolve at build time, not runtime). Acceptable for a single-tenant pilot with no sensitive production data behind it. The stated production pattern is SSO/OIDC, not a bundled key — this mirrors the authentication design decision above.

**Q: How was the deployed system actually verified, not just assumed working?**
A: Two independent live-production audits against the real Azure URL — real HTTP requests, real latency measurements, real citation checks. One audit's latency numbers (multi-second) turned out to be an artifact of the testing tool's own infrastructure, not the app — confirmed by an independent direct timer test showing real latency of 86–461ms. That cross-check is itself documented in the AI usage log as an example of not trusting a tool's output at face value.
