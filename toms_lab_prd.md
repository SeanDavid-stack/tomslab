# Tom's Lab — Product Requirements Document (PRD)

**Version:** 1.0 (spec)
**Project codename:** `tomslab`
**Author:** [your name]
**Date:** April 2026
**Status:** Ready for build

---

## 1. Product Overview

**Tom's Lab** is a desktop application that lets a user search, browse, and learn from a Discord knowledge base — specifically designed around the Bookmap Discord channel `traders-lab-tom-b`, where trader Tom B teaches market mechanics, order flow, and volume profile concepts.

The app ingests Discord chat exports (from DiscordChatExporter / DCE) and uses AI to make the content searchable by meaning, visually browsable by charts, and interactively explorable via conversational Q&A.

**Core value proposition:** Turn a sprawling, unsearchable Discord history into a smart study tool where you can find any concept Tom has taught, see the charts he drew to illustrate it, and ask follow-up questions — all offline-capable with optional free cloud AI.

### Why this exists

Discord is great for live teaching but terrible as a knowledge repository. Messages scroll away, search is primitive, charts live in conversation threads with no index. Students miss sessions. Concepts get explained multiple times over months and you can't piece them together. Tom's Lab solves this for one user (v1) with a path to sharing with a community (v2+).

---

## 2. Goals and Non-Goals

### Goals (v1)
- Ingest DCE JSON exports with full fidelity (messages, threads, images, replies)
- Provide four search modes: keyword, semantic, visual, conversational ("Ask Tom")
- Display results in a Discord-familiar UI with highlighted key speaker
- Run entirely on one user's Windows PC
- Support both free cloud AI (Gemini) and local AI (Ollama)
- Be polished enough to hand to Tom B as a gift
- Be architected so Tom B could distribute it to his community later

### Non-goals (v1)
- Multi-user hosted version (v2)
- Mobile app
- Discord bot / real-time sync (user re-exports manually)
- Transcribing voice channel audio
- Paid subscription features
- Non-Windows platforms (macOS/Linux is v1.5+)

---

## 3. Target Users

**Primary user (v1):** The builder — a Bookmap Discord member who wants deep personal study of Tom B's teachings.

**Secondary users (if Tom distributes):** Other Bookmap members, trading students, anyone who is already in the public Discord channel and wants a study tool for their own use.

**User profile:**
- Comfortable installing Windows software
- Not a developer
- Wants results, not configuration hell
- Cares about trading concepts, charts, and patterns
- Values privacy (doesn't want content leaving their machine by default)

---

## 4. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Tom's Lab (PyQt6 App)                   │
│                                                               │
│  ┌────────────┐  ┌────────────┐  ┌─────────────────────┐    │
│  │  Ingest    │  │  Search UI │  │  Ask Tom (RAG Chat) │    │
│  │  (DCE →    │  │  (4 modes) │  │                     │    │
│  │   SQLite)  │  │            │  │                     │    │
│  └─────┬──────┘  └──────┬─────┘  └──────────┬──────────┘    │
│        │                │                    │                │
│        ▼                ▼                    ▼                │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              AI Provider Abstraction Layer           │   │
│  │  ┌─────────┐  ┌──────────┐  ┌────────┐  ┌────────┐  │   │
│  │  │ Gemini  │  │  Ollama  │  │ Claude │  │ OpenAI │  │   │
│  │  │ (free)  │  │ (local)  │  │ (paid) │  │ (paid) │  │   │
│  │  └─────────┘  └──────────┘  └────────┘  └────────┘  │   │
│  └──────────────────────────────────────────────────────┘   │
│        │                │                                     │
│        ▼                ▼                                     │
│  ┌────────────────────────────┐  ┌─────────────────────┐    │
│  │  SQLite + sqlite-vss       │  │  Local _assets/     │    │
│  │  (messages, embeddings,    │  │  (chart images,     │    │
│  │   concepts, bookmarks)     │  │   videos, files)    │    │
│  └────────────────────────────┘  └─────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
         ▲
         │
┌────────┴────────────┐
│  User runs DCE      │
│  separately to      │
│  export Discord →   │
│  drops JSON into    │
│  Tom's Lab          │
└─────────────────────┘
```

**Key principles:**
- Everything runs locally on user's machine
- AI calls can be local (Ollama) or cloud (Gemini free tier by default)
- User's data (messages, bookmarks) never leaves their machine unless they opt into cloud AI
- No accounts, no logins, no hosting required for v1

---

## 5. Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| GUI framework | **PyQt6** | Already used by builder (BMBRIDGE); mature; native feel |
| Language | **Python 3.11+** | Fits PyQt6; rich AI/ML library ecosystem |
| Database | **SQLite + sqlite-vss extension** | Single file, zero setup, handles vector search |
| Primary AI (cloud) | **Google Gemini API (free tier)** | Free, fast, has vision, no credit card |
| Backup AI (cloud) | **Groq API (free tier)** | Free, blazing fast Llama 3.3 70B |
| Fallback AI (local) | **Ollama** | Free, offline, private |
| Embeddings | **Gemini `text-embedding-004`** (cloud) or **`nomic-embed-text`** (local via Ollama) | Either works; user picks |
| Vision / chart analysis | **Gemini 2.5 Flash** (cloud) or **LLaVA 1.6** (local via Ollama) | Either works |
| OCR (optional) | **PaddleOCR** | Better than Tesseract for charts |
| Installer | **PyInstaller + Inno Setup** | Same pattern as BMBRIDGE |

### Hardware requirements (target user's PC)
- **Minimum:** Windows 10+, 16 GB RAM, 50 GB free disk
- **Recommended:** Windows 10+, 32 GB RAM, NVIDIA GPU 8 GB VRAM+, SSD
- **Builder's machine** (exceeds recommended): Ryzen 9 5900X, 64 GB RAM, RTX 3080 Ti 12GB ✅

---

## 6. Data Model

### SQLite schema

```sql
-- Raw message storage
CREATE TABLE messages (
    id TEXT PRIMARY KEY,              -- Discord message ID
    channel_id TEXT,
    channel_name TEXT,
    guild_id TEXT,
    guild_name TEXT,
    author_id TEXT,
    author_name TEXT,
    author_nickname TEXT,
    timestamp TEXT,                   -- ISO 8601
    timestamp_edited TEXT,
    content TEXT,
    reply_to_message_id TEXT,         -- for threading replies
    is_pinned BOOLEAN,
    is_featured_speaker BOOLEAN,      -- gold-highlight in UI (configured by user)
    raw_json TEXT,                    -- full original for reference
    imported_at TEXT,
    FOREIGN KEY (reply_to_message_id) REFERENCES messages(id)
);

CREATE INDEX idx_messages_author ON messages(author_name);
CREATE INDEX idx_messages_timestamp ON messages(timestamp);
CREATE INDEX idx_messages_channel ON messages(channel_id);
CREATE VIRTUAL TABLE messages_fts USING fts5(content, author_name);  -- keyword search

-- Attachments (images, videos, files)
CREATE TABLE attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    filename TEXT,
    local_path TEXT,                  -- path in _assets/
    url_original TEXT,                -- Discord CDN URL
    content_type TEXT,                -- image/png etc
    file_size INTEGER,
    width INTEGER,
    height INTEGER,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

-- Conversation windows (the "chunks" we embed and retrieve)
-- Each featured-speaker message gets a window of N before and M after
CREATE TABLE conversation_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anchor_message_id TEXT,           -- the featured-speaker message this is built around
    start_message_id TEXT,
    end_message_id TEXT,
    text_combined TEXT,               -- concatenated text for embedding
    image_count INTEGER,
    FOREIGN KEY (anchor_message_id) REFERENCES messages(id)
);

-- Vector embeddings (using sqlite-vss)
CREATE VIRTUAL TABLE window_embeddings USING vss0(
    embedding(768)                    -- dimension depends on model used
);

CREATE VIRTUAL TABLE image_embeddings USING vss0(
    embedding(512)                    -- CLIP dimension
);

-- Auto-extracted concepts (tags)
CREATE TABLE concepts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,                 -- e.g., "VPOC", "absorption"
    description TEXT,
    extracted_at TEXT
);

CREATE TABLE message_concepts (
    message_id TEXT,
    concept_id INTEGER,
    confidence REAL,
    PRIMARY KEY (message_id, concept_id)
);

-- Image AI-generated descriptions (from vision model)
CREATE TABLE image_descriptions (
    attachment_id TEXT PRIMARY KEY,
    description TEXT,                 -- vision model's description
    ocr_text TEXT,                    -- OCR'd text from chart
    extracted_ticker TEXT,            -- AAPL, ES, etc if detected
    extracted_timeframe TEXT,         -- 5m, 1h, daily if detected
    image_type TEXT,                  -- 'chart', 'meme', 'screenshot', 'emoji', 'avatar', 'unknown'
    image_type_confidence REAL,       -- 0.0 to 1.0
    is_relevant BOOLEAN DEFAULT TRUE, -- user can override AI classification
    model_used TEXT,
    generated_at TEXT,
    FOREIGN KEY (attachment_id) REFERENCES attachments(id)
);

CREATE INDEX idx_image_type ON image_descriptions(image_type);

-- User bookmarks
CREATE TABLE bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT,
    note TEXT,                        -- user's personal note
    tags TEXT,                        -- comma-separated user tags
    created_at TEXT,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

-- App configuration
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
-- Examples:
-- 'featured_speaker_username' = 'tom_b_trades'
-- 'ai_provider_primary' = 'gemini'
-- 'ai_provider_fallback' = 'ollama'
-- 'gemini_api_key' = '...' (encrypted at rest)
-- 'conversation_window_before' = '3'
-- 'conversation_window_after' = '5'
```

### File structure on disk

```
%APPDATA%/TomsLab/
├── data/
│   ├── tomslab.db              # SQLite database
│   └── _assets/                # Chart images, files from Discord
├── logs/
│   └── tomslab.log
├── config/
│   └── settings.json           # App config (API keys encrypted)
└── exports/                    # User-generated exports (PDFs, etc)
```

---

## 7. Features — v1 Core (the initial handoff version)

### 7.1 Setup Wizard (first run)
- Welcome screen explaining what Tom's Lab is
- Prompt for AI provider setup:
  - Option 1: Get free Gemini API key (deep link + instructions)
  - Option 2: Install Ollama (detect if already installed, offer guided install)
  - Option 3: Use own API key (Claude, OpenAI)
- Prompt to import first DCE export (file picker)
- Confirm featured speaker (auto-suggest top poster of interest)

### 7.2 Ingestion
- Drag-and-drop DCE JSON file onto app (or File → Import)
- Progress bar with phases:
  1. Parsing messages (fast)
  2. Resolving attachments (copies images from DCE _assets folder to app's _assets)
  3. Building conversation windows
  4. Generating text embeddings
  5. Generating image embeddings (CLIP)
  6. Generating chart descriptions (vision model)
  7. Extracting concepts
- Incremental imports: re-running DCE export with same channel, app detects new messages only, skips already-processed items

### 7.3 Main UI Layout
```
┌──────────────────────────────────────────────────────────┐
│ [Import] [Bookmarks] [Gallery] [Concepts] [Ask] [Settings]│
├──────────────────────────────────────────────────────────┤
│ Search: [___________________] Mode: [Semantic ▼] [🔍]    │
├──────────────────────────────────────────────────────────┤
│                                                            │
│  Results area — Discord-like message feed                 │
│  Featured speaker messages highlighted in gold             │
│  Charts render inline                                      │
│  Reply threading preserved                                 │
│                                                            │
│  [Load more...]                                            │
└──────────────────────────────────────────────────────────┘
```

### 7.4 Search Modes
1. **Keyword** — SQLite FTS5 full-text search, fast, exact matches
2. **Semantic** — embedding similarity, finds concepts by meaning
3. **Visual** — CLIP text-to-image search; finds charts matching text description
4. **Ask Tom** — conversational RAG (see 7.7)

### 7.5 Bookmark / Favorite System
- Star icon on every message
- Bookmarked messages accessible from "Bookmarks" view
- User can add note + tags to each bookmark
- Bookmarks are searchable/filterable

### 7.6 Chart Gallery View
- Dedicated tab showing only messages that contain chart images
- Filterable by: featured speaker only / date range / concept tag
- Grid or masonry layout
- Click image → see full message + surrounding conversation
- Optional: filter by AI-detected ticker or timeframe

**Image classification (auto-filter junk):**
- During ingestion, the vision model classifies every image as: chart, meme, screenshot, emoji, avatar, or unknown
- Gallery defaults to showing **charts only** — memes, GIFs, reaction images, avatars hidden
- Filter toggle: [Charts only] [All images] [Non-charts only] [Flagged for review]
- User can override any classification (right-click → "This is a chart" / "This is not a chart")
- User overrides improve future classification accuracy
- Ingestion summary shows: "Processed 12,847 images: 4,231 charts, 8,616 non-chart (hidden by default)"
- Non-chart images are NEVER deleted — just hidden from default views

### 7.7 "Ask Tom" — Conversational RAG
- Chat interface — user types a question
- Backend: embed question → retrieve top 10 conversation windows (hybrid semantic + keyword) → feed to LLM with system prompt
- Response includes inline citations `[msg:916502712684793916]` that render as clickable links
- Clicking a citation jumps to that message in the main feed
- System prompt emphasizes: "Only use information from the retrieved messages. Cite specific messages. If you don't know, say so."
- Chat history persists per session

### 7.8 Concept Browser
- Auto-extracted during ingestion via LLM pass
- Tags like: VPOC, absorption, initiative activity, responsive activity, liquidity, order flow, orderbook, auction theory, volume profile, overnight inventory, HVN, LVN, etc.
- Browse as a tag cloud or list
- Clicking a concept → filter main feed to messages tagged with it
- User can manually add/edit/merge concepts

### 7.9 "Explain This Chart" AI Feature
- User uploads any chart image (or right-clicks a chart in the feed)
- Vision model analyzes: describes what it sees, identifies patterns, correlates with similar charts in the database
- Output: description + "similar charts Tom has posted" (CLIP similarity) + "concepts this chart illustrates"

### 7.10 Deduplication & Overlap Detection

Handles three levels of duplication as the user imports more data over time:

**Level 1 — Exact duplicate messages (automatic, silent)**
- Triggered when user re-exports a channel they've already imported
- Detection: Discord's globally-unique message ID
- Action: skip on import, log count of skipped messages
- Assets: already-downloaded chart images are reused (not re-downloaded)
- User-visible: "Import complete. 47 new messages added. 5,136 already in database."

**Level 2 — Cross-posted / reposted duplicates (detected, flagged)**
- Triggered when Tom (or anyone) posts substantially the same content in different places
- Detection signals (any 2+ of):
  - Same author
  - Text similarity > 85% (Levenshtein or embedding cosine)
  - Same attachment file hash (MD5 of downloaded image)
  - Posted within a reasonable window (not 3 years apart — that's Level 3)
- Action: store both messages fully, but create a `message_relations` table linking them as "likely repost"
- User-visible: in search results, duplicates collapse into one result with "(also posted in #other-channel, [expand])" toggle

**Level 3 — Conceptual / semantic duplicates (clustered, navigable)**
- Triggered when the same concept is taught multiple times with different wording
- Detection: conversation windows with embedding cosine similarity > 0.85
- Action: clustering during indexing; mark cluster membership but keep all messages distinct
- User-visible: search results group by cluster; one "representative" result shown with "+3 similar teachings" expand option; user can see evolution over time

**New schema additions:**
```sql
CREATE TABLE message_relations (
    source_message_id TEXT,
    related_message_id TEXT,
    relation_type TEXT,              -- 'exact_repost', 'similar_repost', 'semantic_cluster'
    similarity_score REAL,
    detected_at TEXT,
    user_confirmed BOOLEAN DEFAULT NULL,  -- user can confirm/deny
    PRIMARY KEY (source_message_id, related_message_id, relation_type)
);

CREATE TABLE semantic_clusters (
    cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
    representative_message_id TEXT,
    concept_summary TEXT,            -- LLM-generated: "Tom's teachings on VPOC rejection"
    member_count INTEGER,
    earliest_message_date TEXT,
    latest_message_date TEXT
);

CREATE TABLE cluster_members (
    cluster_id INTEGER,
    message_id TEXT,
    PRIMARY KEY (cluster_id, message_id)
);

-- Attachment hash for cross-post detection
ALTER TABLE attachments ADD COLUMN content_hash TEXT;  -- MD5 of file content
CREATE INDEX idx_attachments_hash ON attachments(content_hash);
```

**User controls:**
- Settings → Deduplication tab
- Toggle each level on/off independently
- Adjust similarity thresholds (85% default)
- "Review detected duplicates" UI — user confirms or denies the app's guesses, improves future detection
- Export/import of user-confirmed duplicate mappings (shareable with friends)

**UI behavior in search results:**
```
Semantic search: "how does Tom handle overnight inventory?"

  ┌─ 📅 Jan 2023 ─────────────────────────────────────┐
  │ tom_b_trades [gold]                                 │
  │ "Overnight inventory imbalance means..."            │
  │ [+2 similar teachings from Mar 2024 and Aug 2024] ▼│
  └─────────────────────────────────────────────────────┘

  ┌─ 📅 Nov 2022 ─────────────────────────────────────┐
  │ tom_b_trades [gold]                                 │
  │ "When you see ON high being taken out..."           │
  │ [cross-posted in #all-markets-bruce same day] ↔    │
  └─────────────────────────────────────────────────────┘
```

---

## 8. Features — v1.5 (post-handoff polish)

- **Compare Tom's views over time** — select a concept, see how Tom's messages about it evolved chronologically. Optional LLM summary: "Tom's view on X in 2022 vs 2025"
- **Export to PDF study guide** — user selects bookmarked messages or a concept → generates a formatted PDF with charts inline, for offline study
- **Daily/weekly digest** — scheduled task that runs DCE incrementally, processes new messages, emails/notifies user with "new Tom posts this week" summary
- **macOS/Linux builds**
- **Theme customization** (dark/light/custom)
- **Export/import bookmark sets** (shareable with friends)

---

## 9. Features — v2 (hosted multi-user, if Tom wants it)

- Web app version (FastAPI backend + React frontend)
- User accounts
- Shared database Tom controls
- Payment integration (Stripe) if Tom wants to monetize
- Admin panel for Tom to manage content visibility

*Out of scope for initial build. Architected for but not built.*

---

## 10. AI Provider Abstraction Layer

This is a critical design decision: the app should have a clean abstraction so any AI provider can be swapped in.

```python
# Conceptual interface
class AIProvider(ABC):
    @abstractmethod
    def embed_text(self, text: str) -> list[float]: ...

    @abstractmethod
    def embed_image(self, image_path: str) -> list[float]: ...

    @abstractmethod
    def chat(self, messages: list[dict], system: str) -> str: ...

    @abstractmethod
    def describe_image(self, image_path: str, prompt: str) -> str: ...

class GeminiProvider(AIProvider): ...
class OllamaProvider(AIProvider): ...
class ClaudeProvider(AIProvider): ...
class OpenAIProvider(AIProvider): ...
```

The user picks which provider is active in Settings. Different tasks can use different providers (e.g., local Ollama for embeddings, cloud Gemini for chat).

**Fallback chain:** if primary provider fails (rate limited, no internet), try fallback. If both fail, fail gracefully with a clear error.

---

## 11. Configuration / Settings UI

- **AI Providers** tab: pick primary/fallback, enter API keys (stored encrypted), test connection button
- **Ingestion** tab: window-before/after sizes, which provider does what (embed, chat, vision)
- **Featured Speaker** tab: username(s) to highlight in gold; can pick multiple
- **Advanced** tab: database location, assets location, log level
- **About** tab: version, license, credits, link to GitHub (if open sourced)

API keys stored using the same encryption pattern BMBRIDGE uses (XOR mask + secure storage).

---

## 12. Build Phases

### Phase 0 — Repo setup (Claude Code session 1, ~1 hour)
- Project scaffolding: folders, `pyproject.toml`, `requirements.txt`, Git repo
- Basic PyQt window that opens and shows "Hello, Tom's Lab"
- README, LICENSE
- GitHub repo created (private for now)

**Deliverable:** App opens and displays a window.

### Phase 1 — Ingestion pipeline (Session 2, ~2-3 hours)
- SQLite schema creation
- DCE JSON parser
- Message + attachment import
- Basic PyQt window that shows imported messages in a list (no search yet)
- Conversation window builder

**Deliverable:** User can import a DCE JSON file and see messages listed.

### Phase 2 — Keyword search + UI polish (Session 3, ~2 hours)
- FTS5 full-text search
- Search bar in UI
- Result rendering with Discord-like styling
- Featured speaker highlighting (gold)
- Inline chart rendering
- Reply threading indicators

**Deliverable:** Functional keyword search with nice UI. Already useful as-is.

### Phase 3 — AI provider layer + embeddings (Session 4, ~3 hours)
- Provider abstraction (Gemini + Ollama at minimum)
- Settings UI for API keys
- Text embedding generation during ingestion
- Semantic search mode

**Deliverable:** Semantic search works. User can switch between Gemini and Ollama.

### Phase 4 — Visual search (Session 5, ~2-3 hours)
- CLIP embedding generation for images
- Visual search mode in UI
- Chart gallery view

**Deliverable:** Visual search and gallery work.

### Phase 5 — Ask Tom RAG (Session 6, ~3 hours)
- Chat UI
- Retrieval pipeline (hybrid semantic + keyword)
- LLM integration with citations
- Click-through to source messages

**Deliverable:** "Ask Tom" feature works end-to-end.

### Phase 6 — Vision descriptions + concepts + deduplication (Session 7, ~3-4 hours)
- Vision model integration for chart descriptions
- Concept extraction pipeline
- Concept browser UI
- "Explain this chart" feature
- **Deduplication engine:** Level 1 (exact — runs during ingestion), Level 2 (cross-post detection via text + image hash), Level 3 (semantic clustering)
- "Review detected duplicates" UI for user confirmation

**Deliverable:** Charts text-searchable, concepts browsable, duplicates handled across all three levels.

### Phase 7 — Bookmarks + polish (Session 8, ~2 hours)
- Bookmark system
- Bookmarks UI tab
- First-run setup wizard
- Error handling polish

**Deliverable:** v1 feature-complete.

### Phase 8 — Installer + handoff prep (Session 9, ~3 hours)
- PyInstaller packaging
- Inno Setup installer
- README + installation guide
- Handoff video recording
- Pre-built database with Tom's content (for Tom's convenience)

**Deliverable:** Windows .exe installer ready to send to Tom.

**Estimated total:** ~9 focused sessions over ~3-5 weekends.

---

## 13. Distribution & Handoff to Tom B

### What Tom gets
1. **`TomsLab_Setup.exe`** — standard Windows installer
2. **`tomslab_bookmap_archive.db`** — pre-built database with his content already indexed (optional; he can regenerate if he prefers)
3. **`README.pdf`** — quick start, FAQ, what it does
4. **`Demo.mp4`** — 5-minute walkthrough video
5. **Source code** — GitHub repo link (private, invite-only until he decides)
6. **Handoff letter** — "This is yours. Do what you want with it. No strings."

### The handoff email (draft later)
Don't pitch. Give. Something like:

> Hi Tom,
>
> I've been in your Bookmap channel for a while and your teachings have genuinely helped me. I'm a product manager, not a trader, so I paid you back the only way I know how — I built you a tool.
>
> Tom's Lab is a desktop app that lets anyone search your Discord teachings with AI. It runs on their own PC, free. I've attached the installer and a demo video.
>
> I'm giving this to you outright — no strings. If you want to share it with your community, go for it. If you want me to take it down and never mention it again, I will. Your content, your call.
>
> Whatever you decide, thanks for the education.
>
> [name]

### What to decide before sending
- Your "ask" if Tom asks back (credit? revenue share? nothing?)
- Whether the GitHub repo is public or private when you send
- Whether you want to mention the ability to open source the tool separately

---

## 14. Security & Privacy

- **API keys** stored encrypted in SQLite (XOR + machine-bound, like BMBRIDGE)
- **No telemetry** by default; opt-in anonymous usage stats only
- **No content uploads** to any third party unless user explicitly configures cloud AI
- **Gemini free tier caveat:** Google may train on free tier usage — surfaced clearly in settings
- **Local-only mode** available by default for privacy-conscious users

---

## 15. Success Criteria

### For v1 (personal use)
- ✅ Can find any Tom B concept by typing a rough description of it
- ✅ Can browse all his charts in a gallery
- ✅ Can ask "how does Tom approach X" and get a cited answer
- ✅ Faster than scrolling Discord for the same task
- ✅ Runs offline if needed

### For v1 (handoff readiness)
- ✅ Installs cleanly on a fresh Windows PC without dev tools
- ✅ Setup wizard works for a non-technical user
- ✅ Demo video captures the "wow" moments in under 5 minutes
- ✅ No obvious bugs in the 10 most common workflows

---

## 16. Open Questions / Decisions Deferred

- Final logo/branding
- Whether to support importing from multiple servers in v1 or keep it single-server
- How aggressive to be with de-duplicating Tom's reposted content
- Whether to include a "community ingest" feature where users can contribute their own DCE exports to a shared database (v2+ consideration)
- Analytics/telemetry: none for v1, revisit for v2

---

## 17. Appendix — Glossary

- **DCE:** DiscordChatExporter, the tool used to export Discord conversations
- **RAG:** Retrieval-Augmented Generation — AI answers grounded in retrieved documents
- **CLIP:** OpenAI's model that produces joint embeddings of images and text
- **Ollama:** Local LLM runner; free, open source
- **Conversation window:** A contiguous group of messages (featured speaker + surrounding context) used as a retrieval chunk
- **Featured speaker:** The primary content creator being studied (Tom B, default)
- **Embedding:** A numerical vector representing text or image meaning, used for similarity search
- **pgvector / sqlite-vss:** Database extensions enabling vector similarity search

---

*End of PRD v1.0*
