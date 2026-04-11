# Context structure in Obsidian

## Vault folder layout

```
<your-vault>/
└── claude-recall/
    ├── _index.md                         ← deduplicated project table (auto-updated)
    └── projects/
        └── setu/                         ← slug derived from project directory
            ├── context.md                ← auto-populated, user can add notes
            └── sessions/
                ├── 2025-01-15_09-42.md
                └── 2025-01-16_14-07.md
```

---

## context.md — permanent project memory

**First-session creation (auto-populated):**

context.md is automatically populated when Claude finishes a session. It detects
the project stack from filesystem (package.json, pubspec.yaml, etc.) and extracts
architecture decisions, gotchas, and current state from the transcript.

```markdown
---
project: setu
directory: /home/sayan/projects/setu
created: 2025-01-15
tags: [claude-recall, context]
---

# setu

## What this is
<!-- auto:what_this_is:start -->
This appears to be a civic health platform with verified donor registration
<!-- auto:what_this_is:end -->

## Stack
<!-- auto:stack:start -->
Flutter · Express.js · MongoDB Atlas · Cloudflare R2 · Firebase
<!-- auto:stack:end -->

## Current state
<!-- auto:current_state:start -->
Last session worked on: Implement the ambulance dispatch endpoint
<!-- auto:current_state:end -->

## Architecture decisions
<!-- auto:architecture:start -->
- Using single Flutter repo with role-based routing
- Cloudflare R2 over Cloudinary for S3-compatible storage
<!-- auto:architecture:end -->

## Gotchas
<!-- auto:gotchas:start -->
- express.raw() MUST precede express.json() for webhook signatures
- MongoDB geospatial: use 2dsphere index, not 2d
<!-- auto:gotchas:end -->

## Environment
<!-- auto:environment:start -->
Env vars: MONGODB_URI, R2_ACCESS_KEY, FCM_PROJECT_ID
Git branch: main
<!-- auto:environment:end -->
```

### Auto-marker system

Content between `<!-- auto:section:start -->` and `<!-- auto:section:end -->` is managed
by claude-recall. It is updated on every session end and on `/recall update`.

Content OUTSIDE these markers is user-owned and never modified. Users can add their
own notes, sections, or inline comments anywhere outside the markers.

**Example — user adding their own notes:**
```markdown
## Stack
<!-- auto:stack:start -->
Flutter · Express.js · MongoDB Atlas
<!-- auto:stack:end -->

My notes: We're considering migrating to Supabase in Q2.

## Custom Section (user-written, never auto-modified)
This entire section is mine. Claude will never touch it.
```

---

## Session note — sessions/YYYY-MM-DD_HH-MM.md

Auto-written by `save_context.py`. Edit it in Obsidian to add detail.

```markdown
---
date: 2025-01-16
time: 14:07
project: setu
directory: /home/sayan/projects/setu
session_id: abc123
turns: 6
tags: [claude-recall, session]
---

# Session 2025-01-16 14:07

## Directory

`/home/sayan/projects/setu`

## Started with

> Implement the ambulance dispatch endpoint with live GPS via Socket.io

## Stats

6 user turns · 12 total messages

## Summary

Started with: Implement the ambulance dispatch endpoint · I've created the dispatch endpoint with Socket.io integration for real-time GPS tracking

## Files mentioned

- `server/routes/dispatch.js`
- `server/sockets/tracking.js`
- `lib/screens/dispatch_screen.dart`
- `lib/services/socket_service.dart`

## Next steps

- [ ] Add rate limiting to dispatch endpoint
- [ ] Test GPS accuracy on Android emulator
```

---

## _index.md — project index (deduplicated)

Each project appears **exactly once**, with accumulated stats across all sessions:

```markdown
---
tags: [claude-recall]
---

# claude-recall — project index

Auto-updated by claude-recall on each session end.

## Projects

| Project | Directory | Sessions | Total Turns | Last Active |
|---------|-----------|----------|-------------|-------------|
| [setu](projects/setu/context) | `/home/sayan/projects/setu` | 5 | 127 | 2025-01-16 14:07 |
| [voiceforge](projects/voiceforge/context) | `/home/sayan/projects/voiceforge` | 2 | 8 | 2025-01-15 11:22 |
```

When a session ends for an existing project, its row is **updated in-place**
(session count incremented, turns accumulated, timestamp refreshed).

When a new project is first seen, a new row is appended.

---

## Slug mapping examples

| Directory | Slug |
|---|---|
| `/home/sayan/projects/setu` | `setu` |
| `/home/sayan/client/acme/dashboard` | `acme-dashboard` |
| `/home/sayan/projects/gitguard` | `gitguard` |
| `/mnt/c/Users/sayan/work/api` | `work-api` |

Generic segments stripped before slugging: `projects repos code src workspace dev work home`

---

## Git / team usage

**Recommended `.gitignore`:**
```gitignore
# Share project memory with the team, not session logs
.claude-recall-sessions/
```

Or commit both if the whole team uses Claude Code — they'll all share the same memory.
