# Privacy and data flow

## Default behavior

The development server binds to `127.0.0.1` by default. Browser microphone frames travel to the local Flask process through the Socket.IO connection. The browser can also save finished transcripts in its own IndexedDB database named `realtime-transcript`; these records stay in that browser profile until the user deletes them or clears site data.

The live page saves original audio locally by default in browser storage, in durable chunks. This is local storage rather than a cloud backup. Export the audio after class if it must survive clearing site data, private browsing cleanup or browser quota eviction.

## Local mode

Local mode runs the selected Whisper-compatible model on the configured computer. Audio is decoded, resampled and processed in memory by the local process. It is not sent to a third-party transcription service by this project.

## Cloud mode

Cloud mode sends short normalized audio windows to the endpoint configured in the backend `.env`. The endpoint receives the audio, model name and language. Choose a provider whose retention, training and regional processing policies are acceptable for classroom recordings. The UI shows a cloud privacy notice when this path is selected.

Auto mode tries local startup first. If the local model cannot load and cloud settings are complete, it falls back to the configured cloud provider. This behavior should be included in the user’s consent decision.

## Translation mode

Translation is a separate text-only path. Google Cloud Translation and Microsoft Translator receive generated caption text, never the original audio. Precise translation can use an injected local model or an explicitly configured OpenAI-compatible cloud model. Real-time translation is off by default; when enabled, only final caption batches are sent.

Translation API keys are read from backend environment variables: `TRANSLATION_GOOGLE_API_KEY`, `TRANSLATION_MICROSOFT_API_KEY` and `TRANSLATION_CLOUD_API_KEY`. They are not placed in browser JavaScript, URLs or public capability responses. If a cloud translation provider is selected, the caption text may leave the computer; the UI displays that provider path before class.

## Secrets and logs

- `CLOUD_API_KEY` and all `TRANSLATION_*_API_KEY` values are read only by backend providers and are never returned by `/api/config/public` or `/api/capabilities`.
- Keep `.env` out of version control and do not paste keys into browser fields.
- The provider code does not intentionally log API keys, raw audio or full caption payloads.
- Application and reverse-proxy access logs may still contain request metadata; operators should configure retention and access controls appropriately.

## Classroom consent

Before recording, follow the rules of the institution and course. Tell classmates or the lecturer when audio is being captured, especially when using cloud mode. The software does not provide legal consent management; responsibility remains with the operator.

## Deletion

Use the delete action on the review page to remove a saved session and its local audio chunks from browser storage. Cloud-side deletion and provider retention are outside this repository and must be handled according to that provider’s policy. Clearing website data, private browsing cleanup, quota pressure or a browser storage failure can also remove local audio; saved subtitles may remain independently.
