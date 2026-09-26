# Saarthi, on Android

A Capacitor shell around the same web client, so the UI is identical to what you have
been testing. 3.9 MB, `ai.saarthi.classroom`, targets SDK 35.

```bash
bash android-app/build.sh      # -> build/Saarthi-demo.apk
```

## What is in the APK and what is not

The interface ships inside it. The **lessons, the video and the tutor do not** — those
need a reachable server, because the tutor is a Gemini call and the lessons are 200 MB
of video. On first launch the app asks for a server address, checks it before starting,
and remembers it. The ⚙ button changes it later; no rebuild needed when a tunnel hands
out a new URL.

## Voice input

Android WebView has no Web Speech API, so this build uses **Android's own recogniser**
through `@capacitor-community/speech-recognition`. One `Speech` interface in `app.js`
with two backends — Web Speech in a browser, native on the phone — so push-to-talk,
barge-in, the language picker and the ask-about-a-selection mic are the same code on
both.

Two manifest entries make it work, and the second is easy to miss:

* `RECORD_AUDIO`
* a `<queries>` element for `android.speech.RecognitionService` — without it Android 11+
  hides the recogniser and voice fails on a device that has one, with no useful error.

Language follows the picker (hi-IN, gu-IN, en-IN, mr-IN, bn-IN, ta-IN, te-IN); the
recogniser uses the same codes. Partial results stream in as the student speaks.

Not yet verified on a physical handset — built and statically checked only.

## Sideloading

Debug-signed, so Android will warn. On the phone: Settings → Apps → Special access →
Install unknown apps → allow for whatever app you used to receive the file.
