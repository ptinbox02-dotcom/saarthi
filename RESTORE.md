# Restoring this project

Everything needed is in this repository and on GitHub. Two things are deliberately not,
and both are secrets or output rather than source.

## From nothing

    git clone https://github.com/ptinbox02-dotcom/saarthi.git
    cd saarthi
    echo "GEMINI_API_KEY=..." > .env
    bash classroom/run.sh            # creates .venv on first run

That is the whole procedure. The server will build its own environment; the lessons and
the ~197 MB of video are in the repo, so there is nothing else to fetch.

## What is not in git, and why

| | |
|---|---|
| `.env` | the API key. Set it locally; on a host it is an environment variable |
| `.venv/` | rebuilt by `run.sh` from `classroom/requirements.txt` |
| `classroom/audit/` | real student questions — not for a public repo |
| `build/` | the APK and the walkthrough video; rebuilt by `apps/android/build.sh` |
| `node_modules/` | restored by `npm install` in `apps/android` |

## Checkpoints

`v0.1-student-demo` is the tagged working state — live tutor, two-way board, notes,
Android build, deployed. Restore with `git checkout v0.1-student-demo`.

Tag before restructuring, never delete a branch that has been pushed, and keep `main`
deployable: Render builds from it.

## Deploying

    RENDER_API_KEY=rnd_... bash classroom/redeploy.sh

autoDeploy does not fire on this service — it was created from a public repo URL through
the API, which installs no GitHub webhook, so Render will serve an old commit and report
itself healthy. The script pushes, triggers, waits, and fails if the live commit does not
match HEAD.

## Environment variables the service needs

Names only; values live in the host's settings.

* `GEMINI_API_KEY` — required for the tutor
* `SAARTHI_PASSCODE` — gates the page, API, media and socket. Unset means no gate
* `SAARTHI_DAILY_ASKS` — shared daily budget for answers (default 300)
* `SAARTHI_AUDIT_DIR` — somewhere writable; `/tmp/saarthi-audit` in the container
