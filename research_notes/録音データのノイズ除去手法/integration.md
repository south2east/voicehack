# Integrating speech noise reduction into voicehack (Node.js + browser realtime STT)

Scope: where and how to add denoising to the existing voicehack pipeline, with trade-offs, code pointers, a streaming STFT design for Node, an evaluation plan, and a phased hackathon plan. Researched 2026-09-26. Local measurements were taken on the dev machine (WSL2, Intel Core i5-13500H, Node v24.15.0, ffmpeg-static 5.3.0, which bundles ffmpeg 7.0.2). No project files were modified.

---

## Q0. What the current pipeline actually does (code map)

### Takeaway
Chrome captures with `getUserMedia({ audio: true })`, so its built-in WebRTC echo cancellation, noise suppression and AGC are **already on by default**. The "raw" MP4 in `recordings/` is therefore already WebRTC-processed. From there, 4096-sample Float32 blocks at the AudioContext rate (usually 48 kHz) become Int16 and go over the WebSocket. The server sends each chunk unchanged to both Chirp 3 (LINEAR16) and an ffmpeg AAC encoder.

### Cited Findings (code pointers; from reading the repo)
- `public/app.js:41`: `navigator.mediaDevices.getUserMedia({ audio: true })`. No constraints are set, so the browser defaults apply (see Q1).
- `public/app.js:48-49`: `new AudioContext()` with no `sampleRate`. The rate is whatever the device or browser picks (typically 48000), and `app.js:55-63` sends it to the server in the `start` message.
- `public/app.js:82`: `createScriptProcessor(4096, 1, 1)`. ScriptProcessorNode is deprecated and runs on the main thread. `app.js:84-89` `onaudioprocess` sends each 4096-sample block, which is about 85 ms at 48 kHz or 8192 bytes. `app.js:91-92` wires `source → processor → destination`.
- `public/app.js:24-34`: `floatTo16BitPCM` clamps and converts Float32 to little-endian Int16.
- `public/app.js:150-154`: the UI shows a single download link built from `session.audioUrl`. `public/index.html:32` has one `<a id="audioDownload">` (MP4). There is no UI slot for a second (raw vs denoised) file yet.
- `server.js:304-307`: binary WS frames go straight to `session.writeAudio(data)`. `server.js:317-323` creates the session with `sampleRateHertz: msg.sampleRate || 48000`.
- `server.js:50-68`: `buildStreamingConfig` sets `explicitDecodingConfig: { encoding: 'LINEAR16', sampleRateHertz, audioChannelCount: 1 }` for Chirp 3.
- `server.js:202-218`: `writeAudio(chunk)` is the single fan-out point. It writes the same Buffer to `this.geminiStream` (`{audio: chunk}`) and to `this.ffmpegProcess.stdin`. **This is the natural hook for server-side denoising.**
- `server.js:99-135`: `_startRecording` spawns ffmpeg `-f s16le -ar <rate> -ac 1 -i pipe:0 -c:a aac -b:a 128k -movflags +faststart <file>.mp4`. There is no `-af`, so adding an ffmpeg filter here is a one-line change.
- `server.js:246-271`: `_stopRecording` closes stdin and waits for `close`. `server.js:273-286` `toJSON` exposes a single `audioFile`/`audioUrl`. `server.js:37` serves `recordings/` statically.
- `server.js:165-177`: the STT stream restarts every 4 min. A stateful denoiser must live on the **session**, not on the recognize stream, so its state survives restarts.
- `README.md:71`: the known limitations already note that ScriptProcessor should become an AudioWorklet.
- Bundled ffmpeg 7.0.2 (checked locally with `ffmpeg -filters`) includes `afftdn`, `afwtdn`, `anlmdn`, `arnndn`, `adeclick`, `agate`, `highpass`, `speechnorm`, `dynaudnorm`, `loudnorm` and `silenceremove`. No separate ffmpeg install is needed.

### Inferences
- There are four independent insertion points: (a) capture constraints `app.js:41`, (b) the browser DSP node between `app.js:91` source and processor, (c) the server `writeAudio` `server.js:202`, (d) the ffmpeg args `server.js:108-120` or offline after `_stopRecording`.
- `ws` delivers `Buffer`s that can sit on an unaligned `byteOffset` inside a pooled ArrayBuffer. A server-side processor needs to copy the bytes (for example `new Int16Array(Uint8Array.from(buf).buffer)`) or handle alignment, and it must carry over an odd trailing byte if one ever arrives.

### Gaps
- I did not run the app in a browser, so the actual `sampleRate` and `track.getSettings()` values on the user's machine are unverified. Log `mediaStream.getAudioTracks()[0].getSettings()` to confirm.

---

## Q1. Insertion point (a): getUserMedia constraints (`noiseSuppression`, `echoCancellation`, `autoGainControl`)

### Takeaway
Browser NS is **already enabled** in this app because it uses the defaults. Chrome's NS is WebRTC APM's classic statistical noise-estimate plus Wiener-style suppressor, and it runs after AEC. Google Cloud explicitly advises *against* noise reduction and AGC before STT. So the cheapest experiment is to **turn these OFF** (`{noiseSuppression:false, autoGainControl:false, echoCancellation:false}`) and compare, not to turn them on.

### Cited Findings
- Chrome, Firefox and Safari default to `echoCancellation=true`. Chrome applies `noiseSuppression` and `autoGainControl` by default, Firefox has NS on and AGC off by default, and the article recommends `track.getSettings()` over `getSupportedConstraints()` to check what is actually applied — [addpipe: getUserMedia audio constraints](https://blog.addpipe.com/getusermedia-audio-constraints/)
- `noiseSuppression` is a ConstrainBoolean. A plain `true`/`false` is best effort, and `{exact: …}` fails if the setting can't be met. MDN marks it as "Limited availability" (not Baseline) — [MDN noiseSuppression](https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackConstraints/noiseSuppression)
- In Chrome, software noise suppression runs *after* the echo canceller. Hardware or platform NS before the AEC "will normally impede its performance", and when `echoCancellation` is on, Chrome turns hardware NS off (a macOS-specific experiment in 2017) — [Chrome for Developers: Disabling hardware noise suppression](https://developer.chrome.com/blog/disabling-hardware-noise-suppression)
- WebRTC's NS core (`ns_core`) estimates the noise spectrum using speech/noise likelihoods, including quantile-based noise estimation, and suppresses with a Wiener filter — [WebRTC NS standalone port (cpuimage)](https://github.com/cpuimage/WebRTC_NS/blob/master/noise_suppression.c); [Karatos: WebRTC NS algorithm](https://blog.karatos.in/a?ID=00450-e174364d-1e3b-4db3-b073-a34270b99367)
- Google Cloud STT best practices: "Applying noise-reduction signal processing to the audio before sending it to the service typically reduces recognition accuracy"; "Don't use automatic gain control (AGC)"; use LINEAR16/FLAC, at least 16 kHz, and place the mic close to the speaker — [Google Cloud STT best practices](https://docs.cloud.google.com/speech-to-text/docs/best-practices-provide-speech-data)
- Research on SE front ends for ASR finds that **artifact errors** hurt ASR far more than residual noise. Mixing the enhanced and original signals ("observation adding") monotonically improves signal-to-artifact ratio and ASR — [Rethinking Processing Distortions (arXiv 2404.14860)](https://arxiv.org/abs/2404.14860)
- The `web-noise-suppressor` demo exposes the WebRTC NS and EC constraints as toggles and hardcodes `autoGainControl: false` when feeding its own suppressors — [sapphi-red demo](https://github.com/sapphi-red/web-noise-suppressor/blob/main/demo/src/index.ts)

### Inferences
- Change `app.js:41` to accept constraints from UI checkboxes, for example `getUserMedia({ audio: { noiseSuppression: ns, echoCancellation: ec, autoGainControl: agc, channelCount: 1 } })`. Send the effective `getSettings()` values in the `start` message (`app.js:55-63`) and store them in the session JSON (`server.js:273-286`) so each transcript records which processing was used.
- Recommended design: **STT gets the lightest-processed signal**, with NS and AGC off or at most a mild denoise mixed with the dry signal. **The saved or listening recording can get strong denoising.** This separates the "ASR accuracy" goal from the "clean archive audio" goal.
- Keep `echoCancellation` on only if speakers play audio in the room. In a headset or no-playback setup, turning it off avoids AEC artifacts.

### Gaps
- There is no official Chromium doc describing the 2026 NS implementation in detail. For example, I could not confirm whether any ML-based NS has replaced the classic ns_core in shipping Chrome, so treat the "Wiener-style" description as based on older source code.
- No published measurement of Chirp 3 WER with browser NS on vs off was found. The project needs to measure this itself (Q5).

---

## Q2. Insertion point (b): browser AudioWorklet with WASM RNNoise / Speex / GTCRN or JS spectral subtraction

### Takeaway
Use `@sapphi-red/web-noise-suppressor` (MIT, v0.4.1, updated 2026-09). It ships ready-made AudioWorklet nodes for NoiseGate, RNNoise (WASM and SIMD), Speex preprocess and GTCRN. Moving to an AudioWorklet also removes the deprecated ScriptProcessor (`app.js:82`). The cost is that the build must serve the `.wasm` and worklet JS files from `public/`, and processing uses client CPU.

### Cited Findings
- The package provides `NoiseGateWorkletNode`, `RnnoiseWorkletNode` (xiph/rnnoise via shiguredo/rnnoise-wasm), `SpeexWorkletNode` (speexdsp preprocess) and `GtcrnWorkletNode`. It requires native AudioWorklet, which cannot be polyfilled — [npm @sapphi-red/web-noise-suppressor](https://www.npmjs.com/package/@sapphi-red/web-noise-suppressor); [README](https://github.com/sapphi-red/web-noise-suppressor/blob/main/README.md)
- Usage pattern: `loadRnnoise({ url, simdUrl })`, then `ctx.audioWorklet.addModule(rnnoiseWorkletPath)`, then `new RnnoiseWorkletNode(ctx, { wasmBinary, maxChannels })`, with source → node → destination. Files: `rnnoiseWorklet.js`, `rnnoise.wasm`, `rnnoise_simd.wasm`, `gtcrnWorklet.js`, `gtcrn.wasm`, `speexWorklet.js`, `speex.wasm` — [demo source](https://github.com/sapphi-red/web-noise-suppressor/blob/main/demo/src/index.ts)
- Alternatives on npm (checked locally with `npm view`): `@shiguredo/rnnoise-wasm` 2025.1.5 (Apache-2.0) and `@jitsi/rnnoise-wasm` 0.2.1 — [jitsi/rnnoise-wasm](https://github.com/jitsi/rnnoise-wasm)

### Inferences
- Integration sketch for this repo, which has no bundler: copy the needed `dist` files from `node_modules/@sapphi-red/web-noise-suppressor` into `public/vendor/` (or `app.use('/vendor', express.static(...))` next to `server.js:36`). Load them with an ES-module `<script type="module">` at `index.html:40`, then build `source → rnnoiseNode → pcmTapWorklet`. The small custom `pcmTapWorklet` does the Float32→Int16 conversion and `port.postMessage`s the chunks, replacing `app.js:82-92`.
- RNNoise is designed around 48 kHz with 10 ms (480-sample) frames. Create `new AudioContext({ sampleRate: 48000 })` so the node does not resample, and keep the server's `sampleRateHertz` consistent.
- For an A/B experiment, you can send **two channels** (dry and denoised) interleaved, or two WS streams, so the server records both. This doubles upload bandwidth (48 kHz Int16 mono is about 768 kbit/s per stream).
- A from-scratch JS spectral subtraction worklet is possible, but AudioWorklet delivers 128-sample render quanta. You then need an internal ring buffer and STFT with the same logic as Q3, adding one hop plus the frame of latency. It is fine for learning, but RNNoise or GTCRN are better quick wins.

### Gaps
- I could not find published CPU or latency numbers for GTCRN in this package, or a statement on whether RNNoise nodes internally resample for non-48 kHz contexts.
- Firefox and Safari AudioWorklet + WASM SIMD behaviour was not tested.

---

## Q3. Insertion point (c): server-side streaming denoise before STT, including STFT overlap-add in Node

### Takeaway
There are two practical options. **(c1)** Spawn a second ffmpeg as a streaming filter (`s16le stdin → -af arnndn/afftdn → s16le stdout`) and feed its stdout to Chirp. This needs no DSP code and the filters run far faster than realtime. **(c2)** Write a from-scratch STFT weighted overlap-add (WOLA) processor in JS with `fft.js`. A local benchmark shows about 0.6 % of one core per 48 kHz stream, so performance is a non-issue.

### Cited Findings
- `fft.js` (MIT, v4.0.4 per `npm view`) provides `new FFT(N)`, `realTransform`, `completeSpectrum` and `inverseTransform` — [fft.js on npm](https://www.npmjs.com/package/fft.js). `kissfft-js` 0.1.8 also exists on npm (version per `npm view`; not benchmarked).
- **Local benchmark (this machine):** streaming sqrt-Hann WOLA, N=1024, hop=512, a spectral-subtraction/Wiener gain with a recursive noise tracker, fed 4096-sample Int16 chunks. 60 s of 48 kHz audio took 352 ms CPU, **RTF ≈ 0.006**. The script is in this session's scratchpad (`bench/bench.js`) and is not part of the repo.
- **Local benchmark of ffmpeg filters** on a 60 s 48 kHz mono WAV: `afftdn=nr=12:nf=-40:tn=1` took 0.79 s wall, `highpass=f=80,afftdn=tn=1` 0.69 s, `arnndn=m=sh.rnnn` 1.56 s, and `anlmdn=s=0.001` 4.40 s. All are faster than realtime, and anlmdn is about 5× slower than the others.
- `afftdn` options: `nr` (0.01–97 dB, default 12), `nf` (−80 to −20 dB, default −50), `nt` noise type (default white), `tn` noise-floor tracking (off by default), `ad` adaptivity (0–1, default 0.5), `bn` 15-band custom profile, `gs` gain smoothing (0–50, default 0, reduces musical noise) — [FFmpeg filters doc](https://ffmpeg.org/ffmpeg-filters.html)
- `arnndn`: `model`/`m` is always required. `mix` is between −1 and 1 with default 1; negative values keep the removed noise, and −1 outputs noise only — [arnndn doc (7.0 mirror)](https://ayosec.github.io/ffmpeg-filters-docs/7.0/Filters/Audio/arnndn.html)
- `.rnnn` model files (about 300 KB each: `bd`, `cb`, `lq`, `mp`, `sh`, `std`) are in [richardpl/arnndn-models](https://github.com/richardpl/arnndn-models) and [GregorR/rnnoise-models](https://github.com/GregorR/rnnoise-models/issues/4). `sh.rnnn` downloaded locally (297,646 bytes) and ran with the bundled ffmpeg.

### Streaming STFT/OLA design for arbitrary-size Int16 chunks (for `server.js:202`)
- Keep per-session state on `TranscriptionSession` (constructed at `server.js:70-89`): `inBuf` Float32[N], `fill` count, `outAcc` Float32[N], noise PSD Float32[N/2+1], and a `carryByte` for odd-length Buffers.
- `push(buf)`: copy to an aligned Int16Array, then loop. Copy `min(N-fill, remaining)` samples into `inBuf`. When `fill === N`, run `processFrame()`: window, FFT, compute gain per bin, apply gain to bins k and N−k, IFFT, window, accumulate into `outAcc`, emit the first `hop` samples, then shift both buffers by `hop` and set `fill = N-hop`.
- Using sqrt-Hann for both analysis and synthesis at 50 % overlap gives perfect reconstruction when gain=1. That is a good unit test: the output equals the input delayed by N−hop samples.
- Latency is one frame (N=1024 → 21 ms at 48 kHz) plus waiting for hop-aligned input. That is negligible compared with the 85 ms ScriptProcessor chunk. Emit output as Int16 Buffers of whatever size is ready. Chirp accepts arbitrary chunk sizes, and ffmpeg stdin is a byte stream, so output chunks do not need to match input chunks.
- For noise PSD estimation, use minimum statistics, a VAD-gated recursive average, or the first 0.5 s of the session. For the gain, use a Wiener or decision-directed a-priori SNR gain with a floor (e.g. −10 to −15 dB) to limit musical noise and artifacts. Given the artifact findings in Q1, keep the floor high for the STT branch.
- For `finish()` (`server.js:226`), flush by zero-padding one last frame before `_stopRecording()` so the tail is not lost.

### Inferences
- Option (c1) wiring: in `_startRecording`, spawn `ffmpeg -f s16le -ar R -ac 1 -i pipe:0 -af "highpass=f=80,arnndn=m=models/sh.rnnn:mix=0.7" -f s16le pipe:1`. Pipe `proc.stdout.on('data', b => this.geminiStream?.write({audio: b}))` and change `writeAudio` to write only to that proc. ffmpeg's stdio buffering may add latency. Try `-fflags nobuffer -flush_packets 1` and measure.
- Option (c2) is the "from-scratch algorithm" deliverable. It is pure JS, has no native deps, and is easy to explain in a demo.
- Put the denoiser on the session, not the recognize stream, so it survives `_restartStream` (`server.js:165`).

### Gaps
- I did not measure the end-to-end latency added by ffmpeg stdout piping. I also did not measure `arnndn` quality or its effect on Japanese Chirp 3 WER.
- `kissfft`-family WASM packages were not benchmarked. Given RTF 0.006 with fft.js, they are likely unnecessary.

---

## Q4. Insertion points (d)/(e): post-processing saved recordings, and saving raw + denoised

### Takeaway
The least risky place to denoise is the recording, because the STT path stays untouched. One ffmpeg process can write both files: `-filter_complex "[0:a]asplit=2[raw][d];[d]highpass=f=80,afftdn=nr=12:tn=1[dn]" -map "[raw]" raw.mp4 -map "[dn]" denoised.mp4`. Alternatively, keep a lossless raw WAV or FLAC and run a denoise script after `_stopRecording`.

### Cited Findings
- afftdn, anlmdn and arnndn options and model files: see Q3 — [FFmpeg filters doc](https://ffmpeg.org/ffmpeg-filters.html); [arnndn-models](https://github.com/richardpl/arnndn-models)
- Google recommends lossless LINEAR16/FLAC for STT — [Google Cloud STT best practices](https://docs.cloud.google.com/speech-to-text/docs/best-practices-provide-speech-data). This is relevant if recordings will be re-transcribed or used as an evaluation reference, because AAC 128k is lossy.
- The bundled ffmpeg 7.0.2 includes all of these filters (local `-filters` check, Q0).

### Inferences
- Minimal change for (d): add `-af highpass=f=80,afftdn=nr=12:nf=-40:tn=1` at `server.js:114-115`. The saved MP4 is denoised and STT is unaffected.
- (e) Both versions: use `asplit` with two outputs as above, plus a lossless `raw.flac` (`-c:a flac`) for evaluation. Extend `toJSON` (`server.js:283-284`) to `audioFiles: { raw, denoised }`, and add a second download link or `<audio>` players at `index.html:32` / `app.js:150-154` for an in-browser A/B demo.
- Offline route: after `_stopRecording` resolves (`server.js:237`), spawn `ffmpeg -i raw.flac -af arnndn=... denoised.mp4`. You could then optionally re-run *batch* STT on the denoised file and store a second transcript for comparison.
- `.gitignore` covers only `recordings/*.mp4`. `.flac`/`.wav` outputs would need adding, and model files would need a `models/` dir.

### Gaps
- The perceptual quality of afftdn vs arnndn vs anlmdn on this app's real recordings was not evaluated. Only speed was measured.

---

## Q5. Before/after evaluation

### Takeaway
Build a small offline harness. Mix clean Japanese speech (JSUT/JVS) with noise (DEMAND/MUSAN) at controlled SNRs such as 0/5/10/20 dB. Run each denoiser, then compute (1) intrusive metrics PESQ-WB, STOI and segmental SNR against the clean reference, (2) non-intrusive DNSMOS for real recordings without a reference, and (3) **Chirp 3 CER** on noisy vs denoised. For Japanese, use CER rather than WER, because there are no word boundaries. The ASR metric is the one that matters for this app.

### Cited Findings
- `pesq` (PyPI, MIT) supports only fs=8000 (nb) or 16000 (wb): `pesq(fs, ref, deg, 'wb')` returns P.862.2 MOS-LQO. Resample 48k audio to 16k first — [pesq on PyPI](https://pypi.org/project/pesq/)
- `pystoi` is a pure-Python STOI available via pip. DNSMOS P.835/P.808 ONNX models from microsoft/DNS-Challenge run with onnxruntime and librosa, and wrappers exist (speechmos, torchmetrics `dnsmos`) — [jonnor/machinehearing audio-quality](https://github.com/jonnor/machinehearing/blob/master/audio-quality/README.md); [torchmetrics dnsmos.py](https://github.com/Lightning-AI/torchmetrics/blob/master/src/torchmetrics/functional/audio/dnsmos.py); [DNSMOS P.835 paper](https://arxiv.org/pdf/2110.01763)
- `pysepm` implements Loizou's metrics, including segmental SNR, fwSNRseg, LLR and composite measures — [pysepm](https://github.com/schmiph2/pysepm). [ftshijt/speech_evaluation](https://github.com/ftshijt/speech_evaluation) is a combined toolkit.
- JSUT has about 10 h from a single speaker, and JVS has about 30 h from 100 speakers in 3 styles. Both are free Japanese corpora, with licence terms in each corpus's LICENCE file — [JSUT/JVS paper (J-STAGE)](https://www.jstage.jst.go.jp/article/ast/41/5/41_E1950/_pdf); [JVS arXiv](https://arxiv.org/abs/1908.06248)
- MUSAN has 109 h of music, speech and noise — [MUSAN (arXiv 1510.08484)](https://arxiv.org/pdf/1510.08484). DEMAND provides multichannel environmental noise recordings used in SE train/dev/eval splits — [see usage in Sidon paper](https://arxiv.org/pdf/2509.17052)
- SE artifacts hurt ASR more than residual noise, so report ASR error alongside PESQ/STOI, and try dry/wet mixing — [arXiv 2404.14860](https://arxiv.org/abs/2404.14860)

### Inferences
- Harness outline, in `tools/eval/` as Python. Step 1 is `mix.py`: RMS-scale noise to a target SNR with `noise *= rms(s)/(rms(n)*10**(snr/20))` and write 48 kHz WAV.
- Step 2 is `denoise.sh`: for each method (none / browser-NS recorded / afftdn / arnndn / JS-WOLA via a Node CLI wrapping the same class as the server), run the method.
- Step 3 is `metrics.py`: resample to 16k and compute PESQ-WB, STOI, SegSNR and DNSMOS.
- Step 4 is `asr.js`: feed the WAV through the *same* streaming path by reusing `buildStreamingConfig` (`server.js:50-68`), or use batch recognize with the same model.
- Step 5: compute CER with `jiwer.cer` or an edit distance on NFKC-normalised text with punctuation stripped, because `enableAutomaticPunctuation` is on at `server.js:61`.
- The browser-NS condition can't be simulated offline. Record real sessions with constraints on vs off by playing the same noisy file through a speaker, or use Chrome's `--use-file-for-fake-audio-capture` flag. The flag was not verified in this research.
- Report a table: method × SNR → PESQ, STOI, DNSMOS OVRL, CER, and RTF.

### Gaps
- I did not verify the current download URLs or licence details for JSUT/JVS audio (research-use terms) or DEMAND/MUSAN (CC licences). Check these before redistributing mixed data.
- I did not confirm the exact current PyPI names for DNSMOS wrappers (`speechmos` vs others).

---

## Q6. Recommended phased hackathon plan

### Takeaway
Phase 0 is instrumentation and turning browser NS/AGC off (30 min). Phase 1 is an ffmpeg `afftdn`/`arnndn` quick win with raw + denoised recordings (1–2 h). Phase 2 is the from-scratch JS STFT/Wiener denoiser on the server with a toggle for the STT path (half a day). Phase 3 is neural (RNNoise/GTCRN AudioWorklet in the browser, or `arnndn` streaming on the server) plus the evaluation table.

### Cited Findings
- Speeds that make each phase feasible on CPU (local measurements, Q3): JS WOLA RTF about 0.006; afftdn about 0.013; arnndn about 0.026; anlmdn about 0.073 (60 s audio).
- Google's own guidance (no NS/AGC before STT) — [Google Cloud STT best practices](https://docs.cloud.google.com/speech-to-text/docs/best-practices-provide-speech-data); the artifact-vs-ASR finding — [arXiv 2404.14860](https://arxiv.org/abs/2404.14860)
- Ready-made browser neural nodes — [@sapphi-red/web-noise-suppressor](https://www.npmjs.com/package/@sapphi-red/web-noise-suppressor)

### Inferences (plan with code pointers)
1. **Phase 0 (quick, measurable):** At `app.js:41`, add NS/EC/AGC checkboxes and pass them as constraints. Log `getSettings()` and send it with `start` (`app.js:55-63`), then persist it in the JSON (`server.js:273-286`). Record the same noisy scene with the checkboxes on and off, and compare transcripts. This may itself be the biggest ASR win, per Google's guidance.
2. **Phase 1 (quick win for recordings):** At `server.js:108-120`, add `asplit` so the session saves `*_raw.flac` (or `.mp4`) and `*_denoised.mp4` (`highpass=f=80,afftdn=nr=12:nf=-40:tn=1:gs=5` or `arnndn=m=models/sh.rnnn`). Expose both in `toJSON` and in the UI (`index.html:32`, `app.js:150-154`) for an A/B listening demo. STT is untouched, so there is zero regression risk.
3. **Phase 2 (from-scratch algorithm):** Create `lib/stftDenoiser.js`, implementing the design in Q3 (sqrt-Hann WOLA, N=1024/hop=512 at 48k, min-statistics noise PSD, decision-directed Wiener gain, gain floor, and a `mix` dry/wet parameter). Add a unit test for perfect reconstruction at gain=1. Wire it into `writeAudio` (`server.js:202-218`) behind a `denoise` flag in the `start` message. The flag chooses which branch (STT and/or recording) gets processed.
4. **Phase 3 (neural + evaluation):** In the browser, replace ScriptProcessor (`app.js:82`) with AudioWorklet and insert `RnnoiseWorkletNode`/`GtcrnWorkletNode` (AudioContext at 48 kHz). On the server, the alternative is a streaming ffmpeg `arnndn` filter process whose stdout feeds Chirp. Run the Q5 harness and present a method × SNR table of PESQ/STOI/DNSMOS/CER/RTF.
- Default recommendation for the product: **STT gets dry or lightly processed audio** (NS/AGC off, optional `mix`≈0.5–0.7 denoise). Denoise the **saved recording** strongly, and keep the raw file as well.

### Gaps
- There is no empirical evidence yet on whether any denoiser improves Chirp 3 Japanese CER in this app. This must come from Phase 0 and Phase 3 measurements.
