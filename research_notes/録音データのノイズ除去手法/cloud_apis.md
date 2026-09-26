# Cloud APIs / hosted services for speech noise removal (as of Sept 2026)

Target stack context: Node.js server, `@google-cloud/speech` (installed **6.7.1** in this repo), Chirp 3 streaming via Speech-to-Text V2 (`us`/`eu` multi-region), LINEAR16 PCM streamed from browser, recordings saved as MP4/AAC via ffmpeg.

## Q1. Per-service comparison: pricing / free tier, formats, batch vs streaming, latency, Node.js SDK, Japanese, privacy

### Takeaway
The only real-time options usable from a Node server are on-device/in-process SDKs (Picovoice Koala, ai-coustics, Krisp) or Google's built-in Chirp 3 denoiser; everything else (ElevenLabs Voice Isolator, Auphonic, Cleanvoice, Replicate models) is file/batch post-processing suited to cleaning the saved MP4/AAC recordings. Adobe Podcast Enhance still has no public API, and Dolby.io Media Enhance is a legacy API migrating into Dolby OptiView (effectively not available to new users).

### Cited Findings

**Google Cloud Speech-to-Text V2 – Chirp 3 built-in denoiser** (details under Q2)
- `RecognitionConfig.denoiser_config` { `denoise_audio` (bool), `snr_threshold` (float) } exists; Chirp 3 docs say it reduces "background music or noises like rain and street traffic" but "can't remove background human voices"; `snr_threshold` "is deprecated in Chirp3; set to 0.0" — [Chirp 3 model docs](https://docs.cloud.google.com/speech-to-text/v2/docs/chirp_3-model)
- Generic API reference: "Denoiser config. May not be supported for all models and may have no effect." `snr_threshold`: audio with SNR below threshold is not sent to the model; 0 = no filtering — [Python ref DenoiserConfig](https://docs.cloud.google.com/python/docs/reference/speech/latest/google.cloud.speech_v2.types.DenoiserConfig); [Ruby ref](https://docs.cloud.google.com/ruby/docs/reference/google-cloud-speech-v2/latest/Google-Cloud-Speech-V2-DenoiserConfig)
- Note: the denoiser only cleans audio *going into recognition*; it does not return denoised audio, so it does not help the saved MP4 recording — [Chirp 3 model docs](https://docs.cloud.google.com/speech-to-text/v2/docs/chirp_3-model) (describes it as denoising "before sending to the transcription model")

**ElevenLabs Voice Isolator / Audio Isolation API**
- Endpoint `POST https://api.elevenlabs.io/v1/audio-isolation` (multipart `audio` file; optional `file_format` = `pcm_s16le_16` or `other`); Node SDK method `client.audioIsolation.convert({...})` — [API ref: convert](https://elevenlabs.io/docs/api-reference/audio-isolation/convert)
- Streaming-response variant `POST /v1/audio-isolation/stream` (still takes an uploaded file; response streamed, MP3 by default) — i.e. not real-time mic input — [API ref: stream](https://elevenlabs.io/docs/api-reference/audio-isolation/stream)
- Input formats: AAC, AIFF, OGG, MP3, OPUS, WAV, FLAC, M4A; video MP4, AVI, MKV, MOV, WMV, FLV, WEBM, MPEG, 3GPP. Max 500 MB, max 1 hour — [Voice isolator capability docs](https://elevenlabs.io/docs/overview/capabilities/voice-isolator) (so the repo's MP4/AAC files can be uploaded directly)
- Cost: 1,000 credits per minute of audio — [Voice isolator docs](https://elevenlabs.io/docs/overview/capabilities/voice-isolator); [Help center](https://help.elevenlabs.io/hc/en-us/articles/26446706351377-How-much-does-Voice-Isolator-cost); API pricing page lists $0.12/min, Starter plan ($6/mo) includes ~8.3 min — [ElevenLabs API pricing](https://elevenlabs.io/pricing/api)
- Zero Retention Mode mentioned on pricing page only for Scribe v2 Medical — [ElevenLabs API pricing](https://elevenlabs.io/pricing/api)

**Picovoice Koala (on-device noise suppression SDK)**
- All processing runs locally ("All voice processing runs locally"); requires a Picovoice AccessKey (free sign-up, no credit card); platforms: Linux x86_64, macOS x86_64/arm64, Windows x86_64/arm64, Android, iOS, Chrome/Safari/Firefox/Edge, Raspberry Pi; Node.js appears in the docs SDK navigation — [Koala docs](https://picovoice.ai/docs/koala/)
- Web package `@picovoice/koala-web` (WASM, v3.0.0) — [npm](https://www.npmjs.com/package/@picovoice/koala-web); [Web quick start](https://picovoice.ai/docs/quick-start/koala-web/)
- Free tier: 100 min/month of Koala; paid plan 10K min/month — [Picovoice pricing](https://picovoice.ai/pricing/) (via search summary)
- Streaming/frame-based design ("Koala Streaming Noise Suppression SDK") — [Koala docs](https://picovoice.ai/docs/koala/)

**ai-coustics**
- Official Node.js bindings `@ai-coustics/aic-sdk` (Rust core via Neon) for speech enhancement + VAD — [npm](https://www.npmjs.com/package/@ai-coustics/aic-sdk); [DeepWiki aic-sdk-node](https://deepwiki.com/ai-coustics/aic-sdk-node)
- Real-time processing "<30 ms"; models Quail (speech enhancement), Rook, Tyto, VAD — [ai-coustics pricing](https://ai-coustics.com/pricing)
- Plans: Startup $135/mo (100k min), Pro $360/mo (300k min), Business $540/mo (500k min), Enterprise from $2,000/mo; a free trial is referenced but no details; no explicit free tier — [ai-coustics pricing](https://ai-coustics.com/pricing). (Search snippets quoted older $149/$399/$599 prices — pricing has changed; trust the live page.)
- Developer platform with API playground + SDK announced — [ai-coustics blog](https://ai-coustics.com/blog/developer-platform-api-playground-sdk)

**Krisp (AI Voice SDK: VIVA for voice agents, RTC SDK)**
- SDKs in C, Python, Node.js, Go, Rust; LiveKit/Pipecat integrations; server-side CPU models; removes background noise and non-primary speakers; claims 46% average WER reduction — [Krisp developers](https://krisp.ai/developers/); [Krisp VIVA](https://krisp.ai/developers/viva/)
- SDK pricing is sales-led ("tell us your use case", trial provided); no public self-serve price — [Krisp developers](https://krisp.ai/developers/); consumer Meeting Assistant prices ($8–15/user/mo) are unrelated — [Krisp pricing](https://krisp.ai/pricing/)

**Auphonic**
- Free: 2 hours processed audio/month, API access included on free tier (but no watch folders/batch productions; free output gets an Auphonic jingle); Noise & Reverb Reduction available on all plans; billed per ms with 3-min minimum; outputs include MP3, AAC; video support — [Auphonic pricing](https://auphonic.com/pricing)
- Batch/file-based only (REST API)

**Cleanvoice AI**
- 30 free minutes, no credit card; pay-as-you-go $11–45 (5–30 h), subscriptions $11–90/mo (10–100 h); "Background Noise Remover"; API exists, custom endpoints in custom plans; no Node SDK mentioned — [Cleanvoice pricing](https://cleanvoice.ai/pricing/)

**Adobe Podcast Enhance Speech (now also in Adobe Firefly)**
- No public API; only integration is Wistia (Sept 2024) — [Cleanvoice blog: Adobe Podcast API review](https://cleanvoice.ai/blog/adobe-podcast-api-review/) (competitor source, biased); [Martechcube on Wistia](https://www.martechcube.com/adobe-podcast-taps-wistia-to-launch-its-ai-powered-enhance-speech-api/)
- Enhance Speech now lives in Firefly web; free plan: files up to 30 min / 500 MB, 1 h/day; output format matches input — [Adobe Firefly FAQ](https://helpx.adobe.com/firefly/web/work-with-audio-and-video/work-with-audio/frequently-asked-questions-about-enhance-speech.html)
- Conflict: one aggregator claims an "official wrapper for Python and Node.js" and Enterprise API keys — [aitoolsdevpro](https://aitoolsdevpro.com/ai-tools/adobe-podcast-guide/); not corroborated by any Adobe source — treat as unreliable.

**Dolby.io Media Enhance → Dolby OptiView**
- Dolby.io rebranded to Dolby OptiView (streaming-focused, debuted NAB 2025); legacy Media APIs (Enhance = noise reduction/leveling/dialog isolation, Analyze, Transcode, Diagnose, Music Mastering) "migrating to the OptiView platform", existing customers contact Dolby — [API Evangelist profile](https://github.com/api-evangelist/dolby-io); [Dolby OptiView GitHub](https://github.com/dolbyio); [OptiView NAB 2025 blog](https://optiview.dolby.com/resources/blog/streaming/dolby-optiview-set-to-debut-at-nab-2025/)
- Node client `@dolbyio/dolbyio-rest-apis-client` v6.1.0 still documented — [API reference](https://api-references.dolby.io/dolbyio-rest-apis-client-node/)

**Replicate-hosted models**
- `resemble-ai/resemble-enhance`: denoiser + enhancer (bandwidth extension); ~$0.021/run (~47 runs/$1), Nvidia T4, predictions ~94 s; open source, can self-host via Docker — [Replicate](https://replicate.com/resemble-ai/resemble-enhance); [GitHub](https://github.com/resemble-ai/resemble-enhance)
- Replicate has an official JS client (general knowledge; not re-verified here). DeepFilterNet: no Replicate pricing found — [emergentmind](https://www.emergentmind.com/topics/deepfilternet)

**Azure**
- Microsoft Audio Stack (MAS) in Speech SDK: DSP noise suppression, AEC, AGC, dereverberation, beamforming; noise suppression "requires microphone arrays for optimal performance; the effect is minimal with a single microphone"; min 16 kHz; **supported only in C++, C#, Java (Windows, Linux) — not JavaScript/Node** — [MS Learn MAS](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/audio-processing-speech-sdk); [Audio processing overview](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/audio-processing-overview)

**AWS**
- Amazon Voice Focus: ML noise suppressor in `amazon-chime-sdk-js`, browser-only (Chrome/Firefox/Chromium/Electron, Safari 14.1+), CPU-intensive, used as a mic transform device in Chime meetings — [Chime SDK docs](https://docs.aws.amazon.com/chime-sdk/latest/dg/using-vfns.html); [Voice Focus guide](https://github.com/aws/amazon-chime-sdk-js/blob/main/guides/09_Amazon_Voice_Focus.md). Using it as a standalone CLI/offline tool is a user-requested, not official, feature — [GitHub issue #2176](https://github.com/aws/amazon-chime-sdk-js/issues/2176)

### Inferences
- Summary table (derived from findings above):

| Service | Mode | Free tier | Node | Fits this app for |
|---|---|---|---|---|
| Chirp 3 `denoiser_config` | streaming + batch, server-side, no audio out | covered by existing GCP STT usage | `@google-cloud/speech` ≥7.2.0 | improving live transcription |
| Picovoice Koala | real-time, on-device | 100 min/mo | yes (Node listed; web WASM pkg) | cleaning PCM before STT and before ffmpeg save |
| ai-coustics | real-time SDK (<30 ms) | trial only | `@ai-coustics/aic-sdk` | same as Koala, paid |
| Krisp VIVA | real-time server SDK | sales-led trial | yes | same, needs sales contact |
| ElevenLabs Voice Isolator | file (streamed response) | small credits | official JS SDK | post-processing saved MP4 |
| Auphonic | file/batch | 2 h/mo incl. API | REST | post-processing saved MP4 |
| Cleanvoice | file/batch | 30 min | REST | post-processing |
| Replicate resemble-enhance | file/batch, slow (~94 s) | pay-per-run | JS client | offline post-processing |
| Adobe Enhance | web UI only | 1 h/day UI | none | manual only |
| Dolby.io Enhance | legacy/migrating | n/a for new users | legacy client | avoid |
| Azure MAS | mic DSP | – | not in JS | not usable |
| AWS Voice Focus | browser mic transform | – | browser JS | client-side mic only |

- Japanese: all of these are acoustic (language-agnostic) enhancers; none document language restrictions. Chirp 3 supports `ja-JP` and the denoiser page gives no language restriction.
- Privacy: on-device SDKs (Koala, ai-coustics, Krisp) keep audio local; ElevenLabs/Auphonic/Cleanvoice/Replicate upload recordings to third parties (US-hosted); Google denoiser adds no new data processor beyond STT already in use.

### Gaps
- Koala exact Node package name/version (likely `@picovoice/koala-node`), frame size, sample rate and algorithmic latency were not confirmed from docs fetched.
- ElevenLabs free-plan credit amount and whether free plan allows Voice Isolator via API not confirmed on the fetched pricing page; data retention policy for isolation not documented.
- Auphonic / Cleanvoice data-retention policies not checked; Cleanvoice language list not found.
- ai-coustics free-trial length/minutes not stated.

## Q2. Does Google Cloud STT V2 / Chirp 3 have a built-in denoiser, and how to enable it in streaming from Node.js?

### Takeaway
Yes: `RecognitionConfig.denoiserConfig = { denoiseAudio: true, snrThreshold: 0.0 }` works with Chirp 3, but the repo's installed `@google-cloud/speech` 6.7.1 does not contain the field in its protos — upgrade to ≥7.2.0 (latest 8.1.0) first. Streaming support is not explicitly confirmed in docs, but the field lives in the same `RecognitionConfig` embedded in `StreamingRecognitionConfig.config`.

### Cited Findings
- Chirp 3 docs example: `model="chirp_3"`, `denoiser_config={denoise_audio: True, snr_threshold: 0.0}`; `snr_threshold` deprecated in Chirp 3 (set 0.0); removes music/rain/traffic, not background voices; Chirp 3 GA in `us`/`eu` multi-regions — [Chirp 3 model docs](https://docs.cloud.google.com/speech-to-text/v2/docs/chirp_3-model)
- Docs do not explicitly say whether denoiser applies to `StreamingRecognize` — [Chirp 3 model docs](https://docs.cloud.google.com/speech-to-text/v2/docs/chirp_3-model)
- Local verification (npm pack of the published packages, run 2026-09-26): `build/protos/google/cloud/speech/v2/cloud_speech.proto`
  - 6.7.1 (installed in repo): no `DenoiserConfig` at all
  - 7.0.0, 7.1.0: absent
  - 7.2.0 (released 2025-07-10, engines node>=18): `message DenoiserConfig { bool denoise_audio = 1; float snr_threshold = 2; }` and `DenoiserConfig denoiser_config = 16` in `RecognitionConfig`
  - 8.1.0 (latest, 2026-09-08, engines node>=22): same field; local Node is v24.15.0 so either works
  - `StreamingRecognitionConfig` contains `RecognitionConfig config = 1` — [npm @google-cloud/speech](https://www.npmjs.com/package/@google-cloud/speech)
- Pricing for the denoiser (whether extra charge) not stated on the Chirp 3 page — [Chirp 3 model docs](https://docs.cloud.google.com/speech-to-text/v2/docs/chirp_3-model)

### Inferences
- Change for `server.js` `buildStreamingConfig()` after `npm i @google-cloud/speech@^7.2.0` (or ^8):
  ```js
  config: {
    explicitDecodingConfig: { encoding: 'LINEAR16', sampleRateHertz, audioChannelCount: 1 },
    model: 'chirp_3',
    languageCodes: ['ja-JP'],
    features: { enableAutomaticPunctuation: true },
    denoiserConfig: { denoiseAudio: true, snrThreshold: 0.0 },
  },
  ```
- With 6.7.1, protobufjs would likely silently drop the unknown `denoiserConfig` key (no error, no effect) — worth checking the upgrade actually took effect. Major version bumps (7, 8) may have breaking changes (e.g., Node ≥22 for v8); test before committing.
- Because the denoiser only affects recognition, A/B testing should compare transcripts with/without it on noisy clips; it cannot remove overlapping speakers.

### Gaps
- No official statement confirming the denoiser in `StreamingRecognize` (vs `Recognize`/`BatchRecognize`); must be tested empirically.
- No latency impact figures or pricing surcharge documented.

## Q3. Which are practical for a student hackathon (free credits, easy auth)?

### Takeaway
Best order: (1) Chirp 3 `denoiserConfig` — zero new accounts, one config line + library upgrade; (2) Picovoice Koala — free AccessKey, 100 min/mo, local processing so it can clean PCM before both STT and the ffmpeg-saved file; (3) Auphonic (2 h/mo free incl. API) or ElevenLabs Voice Isolator (API key, official JS SDK, accepts MP4/AAC directly) for post-processing saved recordings. Avoid Adobe (no API), Dolby.io (legacy), Krisp/ai-coustics (sales/paid), Azure MAS (no JS).

### Cited Findings
- Picovoice sign-up free, no credit card; 100 min/mo Koala free — [Koala docs](https://picovoice.ai/docs/koala/); [Picovoice pricing](https://picovoice.ai/pricing/)
- Auphonic free 2 h/mo with API access — [Auphonic pricing](https://auphonic.com/pricing)
- Cleanvoice 30 free minutes, no credit card — [Cleanvoice pricing](https://cleanvoice.ai/pricing/)
- ElevenLabs Voice Isolator: 1,000 credits/min, $0.12/min; accepts MP4/AAC up to 500 MB / 1 h; `client.audioIsolation.convert` in JS SDK — [Voice isolator docs](https://elevenlabs.io/docs/overview/capabilities/voice-isolator); [API ref](https://elevenlabs.io/docs/api-reference/audio-isolation/convert)
- ai-coustics has no explicit free tier (from $135/mo); Krisp SDK is sales-led — [ai-coustics pricing](https://ai-coustics.com/pricing); [Krisp developers](https://krisp.ai/developers/)
- Replicate resemble-enhance ~$0.021/run but ~94 s per prediction — [Replicate](https://replicate.com/resemble-ai/resemble-enhance)

### Inferences
- ElevenLabs free-tier credits likely cover only a few minutes of isolation at 1,000 credits/min; budget a paid Starter plan if used heavily.
- For live pipeline: browser → PCM → (Koala in Node) → Chirp 3 (+ denoiserConfig) and → ffmpeg AAC gives clean saved files without any upload to third parties.

### Gaps
- Koala Node integration effort (frame size alignment with the app's PCM chunks) unverified.
- Whether GCP free trial ($300 credits) covers Chirp 3 usage for this project was not re-checked here.
