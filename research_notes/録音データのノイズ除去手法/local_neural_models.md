# Local (on-device, open-source) neural/hybrid speech denoising models — comparison for a Node.js + browser (48 kHz) stack

Context: Node.js server + browser Web Audio, 48 kHz mono Int16 PCM over WebSocket, ffmpeg-static bundled, CPU only, Linux/WSL2, hackathon team. Research date: 2026-09.

## Summary comparison table (per-model facts; each cell is sourced in the sections below)

| Model | Native SR | Size | Quality (reported) | CPU speed / latency | License | JS/Node route |
|---|---|---|---|---|---|---|
| RNNoise (xiph) | 48 kHz, 480-sample (10 ms) frames | standard + "little" (half params) | (no current numbers found) | very light, frame-by-frame | BSD-3-Clause | Browser WASM (@sapphi-red, @shiguredo); ffmpeg `arnndn` (+ GregorR models) |
| DeepFilterNet3 | 48 kHz full-band only | (param count not in README) | VCTK PESQ 3.17, STOI 0.944, CSIG 4.34, CBAK 3.61, COVL 3.77 | RTF 0.19 single-thread i5-8250U; 40 ms latency | MIT / Apache-2.0 | `deep-filter` Rust CLI binary; LADSPA; community WASM npm packages; community ONNX |
| DPDFNet (Ceva, Dec 2025) | 8 / 16 / 48 kHz variants | 2.31–3.63 M params, 0.36–7.17 GMACs | (no numbers found on README) | ~20 ms first output, then ~10 ms/block | Apache-2.0 | sherpa-onnx (Node / WASM), `pip install dpdfnet` |
| GTCRN | 16 kHz (inferred, see Gaps) | 48.2 K params, 33.0 MMACs/s | VCTK PESQ 2.87, STOI 0.940, SISNR 18.83; DNS3 blind DNSMOS OVRL 2.70 | streaming RTF 0.07 on i5-12400 | MIT | sherpa-onnx Node (`npm i sherpa-onnx`); @sapphi-red GtcrnWorkletNode (browser) |
| Meta Denoiser (Demucs) | 16 kHz | dns48 / dns64 / master64 | (not fetched) | RTF 0.8 (H=48, 4-core i5), 1.2 (H=64, single thread) | CC-BY-NC 4.0 (non-commercial); archived 2023-10-31 | Python only |
| NSNet2 (MS DNS baseline) | 16 kHz wideband | ONNX | (not fetched) | RNN, real-time | see repo | ONNX → onnxruntime-node (DIY STFT) |
| Resemble Enhance | 44.1 kHz | (not stated) | (not stated) | not designed/stated for real time | MIT | Python only (offline) |
| SpeechBrain MetricGAN+ / SepFormer-DNS4 | 16 kHz | — | — | offline | Apache-2.0 (SpeechBrain) | Python only |
| MossFormer2_SE_48K (ClearerVoice-Studio) | 48 kHz | — | — | heavy, GPU recommended (unverified) | Apache-2.0 | Python only (offline) |

## Which can run in the browser (WASM) or Node (onnxruntime-node, WASM, native, ffmpeg arnndn)?

### Takeaway
Browser: RNNoise (several WASM wrappers, 48 kHz native) and GTCRN via `@sapphi-red/web-noise-suppressor`, plus community DeepFilterNet3 WASM npm packages are the realistic in-browser options. Node: the lowest-friction routes are (a) ffmpeg-static `arnndn` with an RNNoise `.rnnn` model file (zero new dependencies), and (b) `sherpa-onnx` npm addon which ships GTCRN and DPDFNet speech-enhancement support including 48 kHz DPDFNet models.

### Cited Findings
- `@sapphi-red/web-noise-suppressor` provides four AudioWorklet nodes: `NoiseGateWorkletNode` ("a simple noise gate"), `RnnoiseWorkletNode` ("based on xiph/rnnoise"), `SpeexWorkletNode` ("based on xiph/speexdsp's `preprocess` function") and `GtcrnWorkletNode` ("based on Xiaobin-Rong/gtcrn"); MIT license; "requires AudioWorklet to work"; install `npm i @sapphi-red/web-noise-suppressor`; usage docs are "written only for vite users" (others must load the wasm binaries and worklet modules manually) — [sapphi-red/web-noise-suppressor](https://github.com/sapphi-red/web-noise-suppressor)
- `@shiguredo/rnnoise-wasm`: actively maintained (236 commits on develop), Apache-2.0 wrapper (the generated wasm follows RNNoise's COPYING), built with Emscripten 4.0.8; API `Rnnoise.load()` → `createDenoiseState()` → `processFrame(frame)` → `destroy()`; targets browsers (Node use not documented) — [shiguredo/rnnoise-wasm](https://github.com/shiguredo/rnnoise-wasm)
- RNNoise itself processes "RAW 16-bit (machine endian) mono PCM files sampled at 48 kHz"; offers a standard model and a "little" model with half the parameters; runtime model loading via `rnnoise_model_from_file()`; BSD-3-Clause — [xiph/rnnoise](https://github.com/xiph/rnnoise)
- ffmpeg `arnndn` filter usage: `ffmpeg -i in.mp3 -af "arnndn=m='rnnoise-models/somnolent-hogwash-2018-09-01/sh.rnnn'" out.mp3`; model files from GregorR/rnnoise-models and richardpl/arnndn-models — [richardpl/arnndn-models](https://github.com/richardpl/arnndn-models); [gist example](https://gist.github.com/AnonymerNiklasistanonym/80464ec515522f0a191fccc89e76f24b); [Daniel Opitz blog](https://odan.github.io/2023/08/20/reducing-background-audio-noise.html)
- There is an open xiph/rnnoise issue titled "Support for ffmpeg arnndn filter?" — [xiph/rnnoise#162](https://github.com/xiph/rnnoise/issues/162); the RNNoise README notes the model format changed "since v0.1.1" — [xiph/rnnoise](https://github.com/xiph/rnnoise)
- sherpa-onnx supports speech enhancement (gtcrn, DPDFNet) on many platforms including NodeJS; install `npm i sherpa-onnx`; Node example `test_offline_speech_enhancement_gtcrn.js` with `gtcrn_simple.onnx` — [sherpa-onnx nodejs-addon-examples](https://github.com/k2-fsa/sherpa-onnx/blob/master/nodejs-addon-examples/README.md); [sherpa-onnx npm](https://www.npmjs.com/package/sherpa-onnx)
- sherpa-onnx DPDFNet docs list 8 kHz (`dpdfnet2_8khz`, `dpdfnet8_8khz`), 16 kHz (`dpdfnet_baseline`, `dpdfnet2`, `dpdfnet4`, `dpdfnet8`) and 48 kHz (`dpdfnet2_48khz_hr`, `dpdfnet8_48khz_hr`) models; both offline and online streaming enhancement; bindings list includes JavaScript/WebAssembly — [sherpa DPDFNet docs](https://k2-fsa.github.io/sherpa/onnx/speech-enhancement/dpdfnet.html)
- GTCRN repo notes ONNX support via sherpa-onnx integration (2025-03-10 update) — [Xiaobin-Rong/gtcrn](https://github.com/Xiaobin-Rong/gtcrn)
- DeepFilterNet3 WASM npm packages exist (community): `deepfilternet3-noise-filter` (inlines worker/worklet as blob URLs, "no webpack configuration required"), `deepfilternet3-workers` (Web Workers + AudioWorklets); browser requirements Chrome 91+, Firefox 89+, Safari 16.4+ for SIMD; built from upstream via Rust + wasm-pack `build_wasm_package.sh` — [npm deepfilternet3-noise-filter](https://www.npmjs.com/package/deepfilternet3-noise-filter?activeTab=readme); [libraries.io deepfilternet3-workers](https://libraries.io/npm/deepfilternet3-workers); [livekit-deepfilternet3-noise-filter](https://github.com/phuvinh010701/livekit-deepfilternet3-noise-filter)
- NSNet2 open baseline is published in ONNX format with inference scripts in the Microsoft DNS-Challenge repo (wideband/16 kHz baseline) — [microsoft/DNS-Challenge README](https://github.com/microsoft/DNS-Challenge/blob/dfaf9c9c80e5d67f5b1477ec53f5a2b5a9d77938/README.md); [DNS-Challenge issue #48 (512-STFT/32 ms window ONNX)](https://github.com/microsoft/DNS-Challenge/issues/48); [MediaEnhanced/DNS-NSNet2](https://github.com/MediaEnhanced/DNS-NSNet2)
- Meta Denoiser, Resemble Enhance, SpeechBrain MetricGAN+/SepFormer, ClearerVoice MossFormer2 are distributed as Python packages (`pip install denoiser`, `pip install resemble-enhance`, SpeechBrain HF models, ClearerVoice conda env) — [facebookresearch/denoiser](https://github.com/facebookresearch/denoiser); [resemble-enhance](https://github.com/resemble-ai/resemble-enhance); [speechbrain/metricgan-plus-voicebank](https://huggingface.co/speechbrain/metricgan-plus-voicebank); [speechbrain/sepformer-dns4-16k-enhancement](https://huggingface.co/speechbrain/sepformer-dns4-16k-enhancement); [MossFormer2_SE_48K](https://huggingface.co/alibabasglab/MossFormer2_SE_48K)

### Inferences
- For this stack the zero-install baseline is: server-side `ffmpeg-static -af arnndn=m=sh.rnnn` on recorded files (offline) — already bundled binary, just download a ~few-hundred-KB model file. Because ffmpeg auto-inserts resampling, 48 kHz Int16 input is fine (RNNoise itself is 48 kHz-native).
- ffmpeg's `arnndn` expects the old GregorR/v0.1-style `.rnnn` text model format; the newer xiph RNNoise (post-v0.1.1) weights likely cannot be loaded directly (hence issue #162). Use GregorR/richardpl models with ffmpeg.
- For the best quality in pure Node without Python: `sherpa-onnx` + DPDFNet 48 kHz (`dpdfnet2_48khz_hr`) or GTCRN (16 kHz; downsample 48k→16k first). This is a prebuilt native addon, so no compilation on WSL2.
- For the browser capture path, `@sapphi-red/web-noise-suppressor` (RNNoise or GTCRN worklet) inserted before the existing PCM-to-WebSocket worklet is the lowest effort; the `AudioContext` is already 48 kHz, which matches RNNoise.
- Microsoft NSNet2 in onnxruntime-node is possible but requires re-implementing STFT/feature extraction/ISTFT in JS — more work than sherpa-onnx for a hackathon.

### Gaps
- Could not verify `@jitsi/rnnoise-wasm` and `@timephy/rnnoise-wasm` package status/APIs (not fetched); treat as alternatives with unknown maintenance.
- Did not confirm the exact sample rate `GtcrnWorkletNode` expects inside `@sapphi-red/web-noise-suppressor` (README does not state it).
- No confirmation whether sherpa-onnx's Node addon exposes the *streaming* DPDFNet/GTCRN API or only offline (docs show an online CLI denoiser; Node example shown is offline).

## DeepFilterNet (v2/v3): Rust CLI, LADSPA, ONNX, WASM, 48 kHz full-band

### Takeaway
DeepFilterNet3 is the strongest mature open model that is natively 48 kHz full-band, permissively licensed (MIT/Apache-2.0), and real-time on a single CPU thread (RTF 0.19, 40 ms latency). The easiest server route is the prebuilt `deep-filter` Rust binary called as a subprocess on 48 kHz WAV files; browser use is possible via community WASM npm packages; ONNX for Node is community-only and not an official streaming export.

### Cited Findings
- Voicebank+Demand results: DeepFilterNet PESQ 2.81 / CSIG 4.14 / CBAK 3.31 / COVL 3.46 / STOI 0.942; DeepFilterNet2 PESQ 3.08 / 4.30 / 3.40 / 3.699 / 0.9429; DeepFilterNet3 PESQ 3.17 / 4.34 / 3.61 / 3.77 / 0.944 — [DeepFilterNet3 paper, arXiv 2305.08227](https://arxiv.org/html/2305.08227)
- "real-time-factor of 0.19 on a single threaded notebook CPU" (i5-8250U), overall latency 40 ms — [arXiv 2305.08227](https://arxiv.org/abs/2305.08227)
- README: "Full-Band Audio (48kHz)"; for the CLI "only wav files with a sampling rate of 48kHz are supported"; dual-licensed MIT or Apache-2.0; `deep-filter` pre-compiled binaries on the releases page; `pip install deepfilternet`; LADSPA plugin for a PipeWire virtual microphone; DeepFilterNet2 paper titled "Real-Time Speech Enhancement on Embedded Devices" — [Rikorose/DeepFilterNet](https://github.com/Rikorose/DeepFilterNet)
- WASM conversion support was merged upstream (PR #452); issue #472 (2023-11-26) shared "very raw" examples running DeepFilterNet3 on the web via WASM and via onnxruntime-web — [DeepFilterNet#472](https://github.com/Rikorose/DeepFilterNet/issues/472)
- "DeepFilterNet does not ship an official ONNX export for Node.js"; a TypeScript filter targets onnxruntime-node with a user-supplied `deepfilternet.onnx` — [Patter docs](https://docs.getpatter.com/python-sdk/providers/deepfilternet-filter)
- Community real-time ONNX Runtime implementation: [shimondoodkin/deepfilter-rt](https://github.com/shimondoodkin/deepfilter-rt); Intel OpenVINO conversion: [Intel/deepfilternet-openvino](https://huggingface.co/Intel/deepfilternet-openvino)
- Forks/wrappers for web: [rokio-team/SquordFilter](https://github.com/rokio-team/SquordFilter) (MIT/Apache-2.0 fork), LiveKit integration [livekit-deepfilternet3-noise-filter](https://github.com/phuvinh010701/livekit-deepfilternet3-noise-filter)

### Inferences
- Since the app already produces 48 kHz mono Int16, writing a WAV header and invoking `deep-filter in.wav -o outdir` from Node's `child_process` is a near-zero-friction offline pipeline on Linux/WSL2 (static Rust binary, no Python).
- For live streaming server-side, DeepFilterNet in Node is harder (no official streaming ONNX); DPDFNet via sherpa-onnx (48 kHz, streaming-designed) is the more turnkey alternative.
- In-browser DFN3 via `deepfilternet3-noise-filter` is feasible but heavier than RNNoise; test CPU usage on target laptops.

### Gaps
- DFN3 parameter count/MACs and DNSMOS figures were not in the fetched README/paper table (paper reports only VCTK metrics in Table 1).
- No measured CPU usage numbers for the community WASM builds.

## Other models: quality / size / CPU / latency / sample rate / license

### Takeaway
GTCRN is the ultra-light option (48.2K params, RTF 0.07) with modest quality (VCTK PESQ 2.87); DPDFNet (Dec 2025) is the newest streaming model with official 8/16/48 kHz ONNX/TFLite; Meta Denoiser is 16 kHz, non-commercial and archived; Resemble Enhance and MossFormer2_SE_48K are high-quality but Python/offline-oriented.

### Cited Findings
- GTCRN: "48.2 K parameters and 33.0 MMACs per second" (paper originally reported 23.7K / 39.6 MMACs; updated after including ERB module); VCTK-DEMAND SISNR 18.83, PESQ 2.87, STOI 0.940; DNS3 blind test DNSMOS-P.808 OVRL 2.70, BAK 3.90, SIG 3.00; streaming RTF 0.07 on Intel Core i5-12400; MIT — [Xiaobin-Rong/gtcrn](https://github.com/Xiaobin-Rong/gtcrn)
- DPDFNet: "a family of causal, single-channel speech enhancement models for real-time noise suppression" adding Dual-Path RNN blocks to DeepFilterNet-style design; 8 kHz 2.51–3.56M params, 16 kHz 2.31–3.54M, 48 kHz 2.58–3.63M; ONNX + TFLite; Apache-2.0; "first enhanced output arrives after one full model window (~20 ms)... subsequent blocks ~10 ms additional delay"; paper arXiv 2512.16420 (2025-12-18) — [Ceva-IP/DPDFNet HF](https://huggingface.co/Ceva-IP/DPDFNet)
- DPDFNet 16 kHz variants: baseline 2.31M params / 0.36 GMACs / 8.5 MB TFLite; dpdfnet2 2.49M / 1.35G / 10.7 MB; dpdfnet4 2.84M / 2.36G / 12.9 MB; dpdfnet8 3.54M / 4.37G / 17.2 MB; CLI `pip install dpdfnet`, `dpdfnet enhance noisy.wav enhanced.wav --model dpdfnet4 --attn-limit-db 12` — [ceva-ip/DPDFNet](https://github.com/ceva-ip/DPDFNet)
- Meta Denoiser: waveform encoder-decoder with skip connections; 16 kHz; checkpoints dns48, dns64, master64; RTF 0.8 (H=48, quad-core i5) and 1.2 (H=64, single thread); CC-BY-NC 4.0; repo archived 2023-10-31; live mode via PulseAudio/Soundflower — [facebookresearch/denoiser](https://github.com/facebookresearch/denoiser)
- Resemble Enhance: denoiser + enhancer (restores distortions, extends bandwidth); trained on 44.1 kHz; MIT; `pip install resemble-enhance`; no real-time claims — [resemble-ai/resemble-enhance](https://github.com/resemble-ai/resemble-enhance)
- SpeechBrain SepFormer-DNS4: trained on 1300 h Microsoft DNS-4 at 16 kHz — [speechbrain/sepformer-dns4-16k-enhancement](https://huggingface.co/speechbrain/sepformer-dns4-16k-enhancement); MetricGAN+ trained at 16 kHz single channel on VoiceBank — [speechbrain/metricgan-plus-voicebank](https://huggingface.co/speechbrain/metricgan-plus-voicebank)
- FullSubNet uses 2 future frames (32 ms look-ahead) for DNS-2020; a pre-trained Fast FullSubNet exists (DNS-INTERSPEECH-2020), RTF measured on i7-9700 — [FullSubNet arXiv 2010.15508](https://arxiv.org/pdf/2010.15508); [Fast FullSubNet arXiv 2212.09019](https://arxiv.org/pdf/2212.09019); [fronx/Fast-FullSubNet](https://huggingface.co/fronx/Fast-FullSubNet)
- MossFormer2_SE_48K (Alibaba ClearerVoice-Studio): 48 kHz speech enhancement weights, Apache-2.0 — [HF model card](https://huggingface.co/alibabasglab/MossFormer2_SE_48K); [ClearerVoice-Studio paper (Interspeech 2025)](https://arxiv.org/html/2506.19398v1)
- Other recent lightweight real-time research models: LiSenNet (2024) — [arXiv 2409.13285](https://arxiv.org/pdf/2409.13285)
- NVIDIA NeMo / Maxine: no free local CPU denoiser was investigated in detail (see Gaps).

### Inferences
- Quality ordering on VCTK (same benchmark): DFN3 (PESQ 3.17) > DFN2 (3.08) > GTCRN (2.87) ≈ DFN1 (2.81). GTCRN is ~50× fewer params than DFN-class models.
- Streaming-capable on CPU: RNNoise, GTCRN, DeepFilterNet2/3, DPDFNet, NSNet2, FullSubNet (with 32 ms look-ahead). Offline/heavier: Meta Denoiser (dns64 not real-time single thread), Resemble Enhance, SepFormer, MossFormer2.
- For a commercial or public demo avoid Meta Denoiser (CC-BY-NC).

### Gaps
- No direct head-to-head DNSMOS table covering RNNoise vs DFN3 vs GTCRN vs DPDFNet was found in primary sources during this session; DPDFNet READMEs did not list PESQ/DNSMOS.
- FullSubNet PESQ/params and NSNet2 metrics not retrieved (papers linked but tables not extracted).
- RNNoise's current quality numbers and exact model sizes (standard vs little) not retrieved.
- NVIDIA NeMo speech-enhancement models and "Maxine-free" alternatives were not researched due to tool budget.

## Real-time streaming vs offline; setup difficulty on Linux/WSL2; Python subprocess vs pure Node

### Takeaway
For a hackathon: (1) offline post-processing of saved recordings → `ffmpeg-static` + `arnndn` (zero new deps) or `deep-filter` binary (best 48 kHz quality, single download); (2) live streaming → browser-side RNNoise/GTCRN AudioWorklet or server-side sherpa-onnx (DPDFNet/GTCRN). Python (`pip install deepfilternet`/`dpdfnet`/`resemble-enhance`) works as a subprocess but adds a venv/PyTorch install burden.

### Cited Findings
- RNNoise operates on 48 kHz 16-bit mono PCM (fits the app's format exactly) — [xiph/rnnoise](https://github.com/xiph/rnnoise)
- `deep-filter` ships pre-compiled binaries; CLI accepts only 48 kHz WAV — [Rikorose/DeepFilterNet](https://github.com/Rikorose/DeepFilterNet)
- sherpa-onnx is installed via `npm i sherpa-onnx` and runs without Internet — [sherpa-onnx npm](https://www.npmjs.com/package/sherpa-onnx); [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
- DPDFNet recommended inference path is "CPU-only, ONNX" via the `dpdfnet` Python package — [Ceva-IP/DPDFNet HF](https://huggingface.co/Ceva-IP/DPDFNet)
- web-noise-suppressor requires AudioWorklet and documentation assumes Vite — [sapphi-red/web-noise-suppressor](https://github.com/sapphi-red/web-noise-suppressor)

### Inferences
- Setup difficulty ranking (easiest → hardest) on WSL2 without GPU: ffmpeg arnndn (already bundled) < `deep-filter` binary < `npm i sherpa-onnx` (prebuilt native addon) < browser WASM worklet (asset loading without Vite) < Python venv (dpdfnet, deepfilternet with torch) < Resemble Enhance / ClearerVoice (PyTorch, large models, slow on CPU).
- Streaming chunking: the app's 48 kHz stream should be buffered to the model's frame (RNNoise 480 samples = 10 ms; DPDFNet ~10 ms blocks after ~20 ms warm-up). Latency budget: RNNoise ~10 ms, DPDFNet ~20 ms, DFN3 ~40 ms.
- Keep the raw recording and produce a denoised copy side-by-side, so ASR can use the raw signal (see next section).

### Gaps
- No measured end-to-end timings for these tools on WSL2 specifically.

## Impact of enhancement on ASR accuracy

### Takeaway
Evidence from 2025-2026 studies indicates front-end denoising frequently *hurts* modern large ASR models (Whisper, Parakeet, Gemini) — in one systematic study, all 40 configurations got worse. Denoise for human listening / playback, but send raw (or lightly processed) audio to the cloud ASR unless A/B testing shows a gain.

### Cited Findings
- "When De-noising Hurts" (arXiv 2512.17562): MetricGAN+ (SpeechBrain) applied before Whisper Large-v3, NVIDIA Parakeet-TDT-1.1B, Gemini Flash 2.0, Parrotlet-a on 500 medical recordings × 9 noise conditions; original noisy audio had lower semWER in all 40 configurations; mean +7.83% absolute semWER, range 1.1–46.6%; worst case Gemini under Gaussian noise (46.57%) — [arXiv 2512.17562](https://arxiv.org/html/2512.17562)
- Proposed causes: modern ASR has internal noise robustness learned from noisy data; enhancement adds "spectral smearing, temporal discontinuities and unnatural formant transitions"; authors advise not applying such preprocessing by default and evaluating per task — [arXiv 2512.17562](https://arxiv.org/html/2512.17562)
- The same paper cites prior work: classical systems benefited from preprocessing (Flynn & Jones 2008), benefits diminished with DNN ASR (Delcroix et al. 2013), and Braun & Gamper (2022) found noise suppression can introduce speech distortion harming ASR more than the noise — [arXiv 2512.17562](https://arxiv.org/html/2512.17562)
- SAM-Audio separation before Whisper consistently raised WER/CER vs raw audio on Bengali and English — [arXiv 2603.04710](https://arxiv.org/html/2603.04710)

### Inferences
- Since the app uses Google Cloud Speech-to-Text (Chirp 3), which is a large noise-robust model, the default should be: raw audio → ASR; denoised audio → recording playback/export. If denoising for ASR is tried, prefer mild settings (e.g., DeepFilterNet attenuation limit, DPDFNet `--attn-limit-db 12`) and A/B test WER on the team's own recordings.

### Gaps
- No study found specifically measuring RNNoise/DeepFilterNet3/DPDFNet → Google Chirp WER; the key evidence uses MetricGAN+ and SAM-Audio.
