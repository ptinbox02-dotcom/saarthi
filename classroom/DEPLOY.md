# Deploying the classroom

The app is a single aiohttp process that holds a WebSocket open for the length of each
answer and serves ~206 MB of lesson video. That rules out serverless: pick a host that
runs a container with a persistent process.

**Before anything else, understand what a link costs you.** Every question spends a
Gemini Live turn and a multimodal call on *your billed key*, and the per-model daily
caps do not scale with the plan. Two protections are built in and both are on by
default in the configs here:

* `SAARTHI_PASSCODE` — no passcode, no page, no video, no socket. Unset means no gate,
  which is right for local development and wrong for anything public.
* `SAARTHI_DAILY_ASKS` — one shared daily budget across everyone holding the link
  (default 300). When it runs out the recording still plays and questions refuse
  politely.

Verify both before you share the URL:

```bash
python3 -m pytest classroom/tests/test_gate.py -q      # 14 tests
```

## 1 · Pack the media

The manifests hold absolute paths from the machine each lesson was rendered on, which
exist nowhere else. Copy the videos in beside the app:

```bash
python3 classroom/pack.py          # ~206 MB into classroom/media/
```

## 2 · Pick a host

### Fly.io — closest to the students

`fly auth login` needs an interactive terminal, so that one step cannot be scripted.
Run it yourself, then everything after it can be:

```bash
fly auth login                               # opens your browser, once
fly launch --no-deploy                       # accept the existing fly.toml
fly secrets set GEMINI_API_KEY=... SAARTHI_PASSCODE=...
fly deploy                                   # from the REPO ROOT, not classroom/
```

Deploy from the repo root: the image needs `classroom/` *and* `hb/gclient.py`, so the
build context is the whole repo and `fly.toml` lives at the top.

`primary_region = "bom"` puts it in Mumbai. Machines suspend when idle and wake on the
next request, so an unused preview link costs almost nothing. Fly asks for a payment
method before the first deploy even when the usage is small.

### Render — blueprint, no CLI

Push the repo, then **New → Blueprint** and point it at `classroom/render.yaml`. Set
`GEMINI_API_KEY` and `SAARTHI_PASSCODE` in the dashboard — `sync: false` keeps them out
of the repo. Use **Starter, not Free**: free instances sleep and will drop a WebSocket
mid-answer.

### Any Docker host

```bash
docker build -f classroom/Dockerfile -t saarthi .
docker run -p 8080:8080 \
  -e GEMINI_API_KEY=... -e SAARTHI_PASSCODE=... saarthi
```

## What will not work

* **Vercel / Netlify / Lambda.** The tutor needs a bidirectional socket held open for
  the length of an answer. Vercel's Python WebSocket path is Django/Channels ASGI, so
  this would need porting off aiohttp *and* the media moved to Blob storage.
* **Free tiers that sleep.** A cold start mid-answer closes the socket. The client
  reconnects and the board keeps what is on it, but the answer in flight is lost.

## After it is up

* `GET /gate` is the health check — it is the only route that answers before login.
* `classroom/audit/<topic>.jsonl` records every question, answer, and board op. It is
  written inside the container, so mount a volume if you want to keep it.
* Rotate `SAARTHI_PASSCODE` by setting a new value and redeploying; existing cookies
  stop working immediately, because the cookie *is* the passcode.
