# Layout

One repository, several things that deploy on their own. The repo is the unit of
history; a container is the unit of deployment, and those do not have to be the same.

    core/                model names, retry, the manifest contract — shared, and small
    factory/
      kit/               Manim scenes, narration timing, splicing, the eval gate
      pipeline/          per-lesson steps: audio, render, assemble, export
    classroom/           the student app: server, web, lessons, media, tests
      tools/             the demo recorder
    knowledge/           syllabus corpus and the citation gate (not started)
    apps/android/        Capacitor shell around the same web client

## Why not three repositories yet

The contract between these is still moving — the fact-sheet schema, the manifest, the
board op vocabulary. Three repositories turns every cross-cutting change into three
coordinated pull requests, and the shared pieces drift apart between them.

There is direct evidence in this project's own history: the factory lived in a sibling
directory reached by `sys.path`, and that coupling cost an hour when paths moved and
left the code unversioned for months. Splitting early makes that failure mode the
default.

Split a boundary out when it stops changing. The knowledge layer is the likely first,
because a corpus has a different release rhythm from an app.

## The seam that matters

`classroom/lessons/<topic>.json` is the contract between the factory and the app: beats,
verified facts, safe stopping points, narration, scope. It is the reason the student app
has never needed to know that Manim exists, and the reason the knowledge layer can fill
in `facts` without touching either side.

Keep it explicit. Anything that widens it deserves an argument.

## Restoring

`v0.1-student-demo` is the tagged working state: live tutor, two-way board, notes, APK,
deployed. Tag before restructuring, never delete, and keep `main` deployable — Render
builds from it.
