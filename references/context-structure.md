# Context structure in Obsidian

## Vault folder layout

```
<your-vault>/
└── claude-recall/
    ├── _index.md                         ← auto-updated on every session end
    └── projects/
        └── setu/                         ← slug derived from project directory
            ├── context.md                ← YOU edit this in Obsidian
            └── sessions/
                ├── 2025-01-15_09-42.md
                └── 2025-01-16_14-07.md
```

---

## context.md — permanent project memory

**Scaffold written on first session:**
```markdown
---
project: setu
directory: /home/sayan/projects/setu
created: 2025-01-15
tags: [claude-recall, context]
---

# setu

## What this is

## Stack

## Current state

## Architecture decisions

## Gotchas

## Environment
```

**Example after filling in:**
```markdown
---
project: setu
directory: /home/sayan/projects/setu
created: 2025-01-15
tags: [claude-recall, context]
---

# setu

## What this is
Civic health platform — verified donor registration, blood inventory search,
one-tap ambulance dispatch with live GPS tracking.

## Stack
Flutter (single codebase, role-based routing) · Express.js on Railway ·
MongoDB Atlas · Cloudflare R2 · Firebase Cloud Messaging ·
Nodemailer OTP · Gemini Flash 2.0

## Current state
Auth (email OTP) done. Blood inventory search done.
Working on: ambulance dispatch real-time tracking via Socket.io.

## Architecture decisions
- Single Flutter repo with role-based routing — no separate admin repo
- Cloudflare R2 over Cloudinary for S3-compatible storage
- express.raw() MUST precede express.json() for webhook signature verification

## Gotchas
- FCM: google-services.json goes in android/app/ — never commit it
- MongoDB geospatial: use 2dsphere index, not 2d
- Railway free tier sleeps after 30 min — upgrade before demo

## Environment
Railway project: setu-backend · MongoDB cluster: cluster0
R2 bucket: setu-uploads · FCM project: setu-fcm
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

## _index.md — project index

Auto-appended on every session end:
```markdown
- [setu](projects/setu/context) · `/home/sayan/projects/setu` · 6 turns · 2025-01-16 14:07
- [voiceforge](projects/voiceforge/context) · `/home/sayan/projects/voiceforge` · 4 turns · 2025-01-15 11:22
```

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
