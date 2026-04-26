# Pre-flight checklist — run this 30 min before Tom joins

*Brazil time: do this at 09:30 for a 10:00 call.*

## 30 minutes out

- [ ] **Laptop plugged in.** GPU jobs will chew battery.
- [ ] **Close Slack, Discord, email, notifications.** Tom sees your screen.
- [ ] **Open Tom's Lab** via `Run Tom's Lab.bat`. **Leave the disclaimer dialog up** — Tom sees it cold during the demo (see [04_demo_script.md](04_demo_script.md) at 00:00). Do NOT click through pre-call.
- [ ] **Reset the disclaimer flag** if you've previously accepted it on this machine, so it pops up live for Tom: ask the dev to clear `disclaimer_accepted` in the settings table.
- [ ] **Check AI providers are alive:**
  - Settings → Gemini API key set? Click *Test connection* → expect green tick.
  - Ollama running? (taskbar icon) — it's the offline fallback.
  - If either fails, fix it now. A "ProviderUnavailable" error in front of Tom kills the demo.

## 20 minutes out

- [ ] **Warm Semantic search.** Switch mode to *Semantic*, type `VPOC`, hit enter. First run will trigger an embedding build if one isn't cached — wait for the status bar to finish. Subsequent searches will be instant.
- [ ] **Warm Visual search.** Switch mode to *Visual*, type `chart with three peaks`. Confirms CLIP is loaded and Gallery will have real hits.
- [ ] **Populate Gallery.** Run a broad keyword search (e.g. `Tom`) so the Gallery tab isn't empty when you click it. Empty Gallery looks like a broken app.
- [ ] **Pre-open Ask Tom** and ask one warm-up question so the first-call latency is paid before Tom sees it. Suggested: *"Explain Naked VPOC (NVPOC) and why Tom watches for it."* — this is also your cold-open, so the second run will feel snappy.

## 10 minutes out

- [ ] **Window size.** Maximise Tom's Lab. Don't share a tiny window.
- [ ] **Font size.** If you're sharing to a call, the default reads small on a compressed video feed. Either bump your OS display scale to 125% or verbally say you'll zoom in when needed.
- [ ] **Docs A & B sent.** Email Tom both PDFs before the call starts, subject line: *"Tom's Lab — quick read before our call"*. Don't rely on him reading them live.
- [ ] **Demo script open on your phone or a second monitor**, not the screen you're sharing.
- [ ] **Glass of water.** It's a 45-min talk, you'll dry out.

## 2 minutes out

- [ ] **Breathe.** Tom didn't answer your questions before — that's not a "no", that's a busy subject-matter expert. Today you're making it easy for him.
- [ ] Confirm your tab is **Feed**, scroll position **top**, search bar **empty**. Clean start.

---

## If something breaks mid-demo

| Symptom | What to say | What to do |
|---|---|---|
| Ask Tom hangs | *"The model is slow today; I'll skip ahead and come back."* | Move on; don't cancel mid-answer — Gemini usually finishes in 5–15 s. |
| Provider error | *"AI providers are optional; the keyword and visual search still work entirely offline — let me show those."* | Switch to Keyword/Visual. The non-AI features are the safe ground. |
| Empty Gallery | *"Haven't run a search yet this session."* | Run a broad keyword search, then return to Gallery. |
| Transcription pill shows up | *"That's TomTube indexing your YouTube in the background — no need to watch it."* | Ignore it. |
| App crashes | *"Let me restart — happens once in a while, data's safe on disk."* | Relaunch via the batch file; all state (bookmarks, favourites, index) is persisted. |

---

## After the call

- [ ] Write Tom's answers into `02_questions_for_tom.md` while they're fresh.
- [ ] Note any new questions he raised — those become your next list.
- [ ] Save any off-script reactions ("I'd want X to not be there") — those become feedback-memory entries.
