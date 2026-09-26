# Classical (non-DL) DSP Speech Noise Reduction — implementable from scratch

Scope note: target stack = Node.js server + browser Web Audio, 48 kHz mono Int16 PCM over WebSocket, `ffmpeg-static` bundled (verified locally: `ffmpeg version 7.0.2-static` in `node_modules/ffmpeg-static/ffmpeg`), downstream ASR = Google Cloud Speech-to-Text (Chirp 3).

Source-verification legend: equations for Boll / Berouti / Wiener / Ephraim–Malah / Martin / MCRA are given in their standard textbook form and cited to the original papers (DOI links). The paper PDFs themselves were NOT re-fetched in this session; the decision-directed + log-MMSE equations and default constants WERE verified line-by-line against the `logmmse` Python source (fetched). noisereduce and ffmpeg parameters were verified against source code / the bundled binary.

---

## Q1. How do spectral subtraction, Wiener, MMSE-STSA / log-MMSE and decision-directed a priori SNR work? STFT choices at 16k/48k?

### Takeaway
All four methods share one skeleton: STFT → estimate noise PSD λ_d(k) → compute a real gain G(k,l) ∈ [G_min,1] per time-frequency bin → multiply noisy STFT (keep noisy phase) → iSTFT with overlap-add. They differ only in the gain rule; log-MMSE with decision-directed (DD) a priori SNR (α≈0.98) is the classical "sweet spot" with far less musical noise than power subtraction, and a complete ~50-line reference implementation exists (Python `logmmse`, ported from Loizou's MATLAB).

### Cited Findings

**Common framework (notation)**
- Noisy y(n)=x(n)+d(n); STFT Y(k,l)=X(k,l)+D(k,l). Enhanced X̂(k,l)=G(k,l)·Y(k,l), phase of Y reused. Phase is kept because the ear is relatively insensitive to phase and the MMSE phase estimate is the noisy phase — [Ephraim & Malah 1984, IEEE TASSP 32(6)](https://doi.org/10.1109/TASSP.1984.1164453). SoX-style spectral subtraction likewise "directly synthesizes a new signal using the denoised amplitude spectrum and the original phase spectrum" — [dshanpi wiki on SoX noisered](https://wiki.dshanpi.org/en/blog/sox-rnnoise-denoise/)
- Definitions: a posteriori SNR γ(k,l)=|Y(k,l)|²/λ_d(k); a priori SNR ξ(k,l)=λ_x(k,l)/λ_d(k) — [Ephraim & Malah 1984](https://doi.org/10.1109/TASSP.1984.1164453)

**1. Spectral subtraction (Boll 1979)**
- Magnitude subtraction: |X̂| = max(|Y| − |D̂|, 0), where |D̂| = average noise magnitude measured during non-speech; Boll also proposed magnitude averaging over adjacent frames and "residual noise reduction" (replace a bin with its minimum over neighbouring frames when below max noise residual) — [Boll 1979, IEEE TASSP 27(2)](https://doi.org/10.1109/TASSP.1979.1163209)
- Generalised/power form (textbook): |X̂|^p = max(|Y|^p − α|D̂|^p, β|D̂|^p), p=1 magnitude, p=2 power. Equivalent gain G = max(1 − α(1/γ)^{p/2}, β)^{1/p}.
- Berouti et al. over-subtraction α and spectral floor β: "an over-subtraction factor that controls the amount of noise to be subtracted and a spectral flooring factor that mitigates the 'musical' noise" — [Berouti, Schwartz, Makhoul ICASSP 1979](https://doi.org/10.1109/ICASSP.1979.1170788) (summary via [Columbia EE e4810 project report](https://www.ee.columbia.edu/~dpwe/classes/e4810-2002-09/projects/mg2016/report.html))
- SNR-dependent α (linear in frame SNR, dB): α = α₀ − (3/20)·SNR for −5 dB ≤ SNR ≤ 20 dB, commonly α₀ = 4 (so α=4.75 at −5 dB, α=1 at 20 dB); β ∈ [0.005, 0.1] at high noise, ~0.002 is often cited for low noise. The general linear form "α_r = α₀ + SNR_r·(1−α₀)/SNR₁ … α_r is inversely proportional to SNR_r" — [search summary citing multi-band SS literature / Columbia report](https://www.ee.columbia.edu/~dpwe/classes/e4810-2002-09/projects/mg2016/report.html). (Exact Berouti constants are from memory of the paper/Loizou ch.5; verify against the paper before quoting in the final report.)
- β trade-off: "An increased noise floor parameter β value reduces the perceived noise fluctuation but increases the level of background noise" — [ResearchGate: Multi-band spectral subtraction (Kamath & Loizou)](https://www.researchgate.net/publication/229005750_A_multi-band_spectral_subtraction_method_for_enhancing_speech_corrupted_by_colored_noise)
- Multi-band spectral subtraction (Kamath & Loizou 2002) splits spectrum into ~4 bands with a per-band α_i and an extra band weight δ_i (more subtraction at high freq) to handle coloured noise — [ResearchGate: Kamath & Loizou](https://www.researchgate.net/publication/229005750_A_multi-band_spectral_subtraction_method_for_enhancing_speech_corrupted_by_colored_noise)

**Musical noise — cause**
- "Frame-to-frame random fluctuations in the noise spectrum mean that some bins are over-subtracted (producing zeros or near-zeros) while neighboring bins are under-subtracted (leaving spectral peaks). These isolated peaks sound tonal." Berouti's fix: over-subtract + spectral floor to make the remaining musical sound inaudible — [search summary / Fiveable study guide](https://fiveable.me/advanced-signal-processing/unit-3/spectral-subtraction-noise-reduction/study-guide/3Z2HM65obRJvyEuP)

**2. Wiener filter**
- Gain G_W = ξ/(1+ξ) (a priori-SNR Wiener). With ML/power-subtraction estimate ξ̂=γ−1 this reduces to G = 1 − 1/γ (= power spectral subtraction with α=1). Parametric Wiener G=(ξ/(ξ+μ))^β generalises it — [arXiv 1809.07384 "New insights on optimality of parameterized Wiener filters"](https://arxiv.org/pdf/1809.07384)
- The `logmmse` code computes exactly `A = ksi / (1 + ksi)` (the Wiener gain) as the first factor of the log-MMSE gain — [logmmse source](https://github.com/wilsonchingg/logmmse)

**3. MMSE-STSA (Ephraim–Malah 1984)**
- Estimates amplitude A=|X| minimising E[(A−Â)²] under Gaussian speech & noise STFT model:
  G_STSA(ξ,γ) = (√π/2)·(√v/γ)·exp(−v/2)·[(1+v)·I₀(v/2) + v·I₁(v/2)],  v = ξγ/(1+ξ);
  I₀, I₁ = modified Bessel functions — [Ephraim & Malah 1984](https://doi.org/10.1109/TASSP.1984.1164453). Python reference with this gain: [eesungkim/Speech_Enhancement_MMSE-STSA](https://github.com/eesungkim/Speech_Enhancement_MMSE-STSA). Implementation tip: for large v use exponentially-scaled Bessel (`scipy.special.i0e/i1e`) to avoid overflow; in JS you must code I₀/I₁ yourself (polynomial approximations, e.g. Abramowitz & Stegun 9.8.1–9.8.4).

**4. log-MMSE (Ephraim–Malah 1985)**
- Minimises E[(log A − log Â)²], which is perceptually better matched:
  G_LSA(ξ,γ) = ξ/(1+ξ) · exp( ½ ∫_v^∞ e^{−t}/t dt ) = ξ/(1+ξ)·exp(½·E₁(v)) — [Ephraim & Malah 1985, IEEE TASSP 33(2)](https://doi.org/10.1109/TASSP.1985.1164550)
- Verified code (`logmmse`, Python port of `logmmse.m` via braindead/Noise-reduction; PyPI `logmmse`):
  ```python
  A = ksi / (1 + ksi); vk = A * gammak
  ei_vk = 0.5 * expn(1, vk)      # scipy.special.expn(1,·) = E1
  hw = A * np.exp(ei_vk)
  ```
  — [wilsonchingg/logmmse logmmse.py](https://github.com/wilsonchingg/logmmse); [PyPI logmmse](https://pypi.org/project/logmmse/) (package noted as inactive — [Snyk advisor](https://snyk.io/advisor/python/logmmse))
- JS needs E₁(x): implement via series for x<1 (E₁ = −γ_E − ln x − Σ(−x)^k/(k·k!)) and continued fraction/rational approx for x≥1 (Abramowitz & Stegun 5.1.53/5.1.56). (Standard numerical recipe; not re-verified this session.)

**5. Decision-directed a priori SNR (Ephraim–Malah 1984)**
- ξ̂(k,l) = α·|X̂(k,l−1)|²/λ_d(k,l−1) + (1−α)·max(γ(k,l)−1, 0), α≈0.98, floor ξ_min — [Ephraim & Malah 1984](https://doi.org/10.1109/TASSP.1984.1164453)
- Verified defaults in `logmmse`: `aa = 0.98`, `ksi_min = 10**(-25/10)` (−25 dB), γ clipped `np.minimum(sig2/noise_mu2, 40)`; first frame uses `ksi = aa + (1-aa)*max(gammak-1,0)`; `Xk_prev = (sig*hw)**2` — [logmmse source](https://github.com/wilsonchingg/logmmse)
- Why it kills musical noise: DD smoothing makes ξ̂ follow a heavily smoothed version of γ in noise-only bins, so isolated random γ peaks do not open the gain → residual noise sounds like a lower, stationary hiss rather than tones (Cappé 1994 analysis) — [Cappé 1994, IEEE TSAP 2(2)](https://doi.org/10.1109/89.279278)

**STFT frame/hop/window**
- `logmmse` default: frame = 20 ms (`Slen = floor(0.02*Srate)`, made even), 50 % overlap (`PERC = 50`), Hann window scaled for OLA (`win = win*len2/sum(win)`), zero-padded FFT of `nFFT = 2*Slen`, first `noise_frames=6` frames used as initial noise — [logmmse source](https://github.com/wilsonchingg/logmmse). At 16 kHz → 320 samples, FFT 640; at 48 kHz → 960 samples, FFT 1920 (numpy handles non-power-of-2; fft.js requires power of 2, so use 1024/2048).
- noisereduce default `n_fft = 1024` (at 48 kHz ≈ 21 ms; at 16 kHz = 64 ms) — [noisereduce README](https://github.com/timsainb/noisereduce)
- ffmpeg `afwtdn` default frame `samples=8192`; `anlmdn` patch 2 ms, research 6 ms — bundled `ffmpeg -h filter=…` (7.0.2).

### Inferences
- Recommended hackathon settings: 48 kHz → frame 1024 (21.3 ms), hop 512 (50 %) with √Hann analysis+synthesis (or Hann analysis / rectangular synthesis at 50 %) or Hann with hop 256 (75 %) for smoother gains; 16 kHz → frame 512 (32 ms), hop 256 or frame 320/hop 160 (20/10 ms). Speech-enhancement literature standard is 20–32 ms frames, 50–75 % overlap.
- Since Google STT recommends 16 kHz, a plausible pipeline is: resample 48k→16k first (ffmpeg `aresample`), then denoise at 16k with N=512 → 3× less compute.
- Implementation priority for a student team: (1) power spectral subtraction with Berouti α/β (≈30 lines); (2) swap gain to Wiener with DD ξ (≈+5 lines, big musical-noise improvement); (3) log-MMSE (needs E₁). Step 2 gives most of the benefit.
- Add gain floor G_min ≈ 0.1–0.3 (−20…−10 dB) rather than zero — keeps a natural noise bed and is kinder to ASR (see Q6).

### Gaps
- Exact Berouti constants (α₀=4, slope −3/20, β ranges) and Loizou's recommended values were not re-verified from the paper/book text in this session (UTD Loizou PDF fetch failed with TLS error).
- No quantitative head-to-head (PESQ/STOI) numbers for these four methods were collected here; Loizou's book ch. 12 has them.

---

## Q2. Noise estimation: fixed profile vs minimum statistics vs MCRA/IMCRA; VAD-based updates

### Takeaway
For a hackathon: start with a fixed noise profile from the first 0.1–0.5 s of each recording (what `logmmse`, SoX, noisereduce-stationary and ffmpeg `afftdn sn` do), plus a cheap VAD-gated recursive update (`logmmse`'s LLR VAD with μ=0.98). Minimum statistics / MCRA are the right choice only if noise changes over time and there is no guaranteed leading silence; MCRA is the simplest of the adaptive ones.

### Cited Findings
- **Fixed profile (leading silence):** `logmmse` averages |FFT| of the first 6 frames: `noise_mean += abs(fft(win*x[j:j+Slen]))`, `noise_mu2 = (noise_mean/noise_frames)**2` — [logmmse source](https://github.com/wilsonchingg/logmmse). SoX: run `noiseprof` "on a section of audio that ideally would contain silence but in fact contains noise — typically found at the beginning or the end of a recording" — [sox(1) man page](https://linux.die.net/man/1/sox)
- **VAD-gated update (logmmse):** per frame compute log-likelihood ratio `log_sigma_k = gammak*ksi/(1+ksi) - log(1+ksi)`; `vad_decision = sum(log_sigma_k)/Slen`; if `vad_decision < eta` (eta=0.15) then `noise_mu2 = mu*noise_mu2 + (1-mu)*sig2` with `mu=0.98` — [logmmse source](https://github.com/wilsonchingg/logmmse). (This is the Sohn-style statistical-model VAD.)
- **Minimum statistics (Martin 2001):** tracks spectral minima per band "without any distinction between speech activity and speech pause"; derives an optimal time-varying smoothing parameter by minimising a conditional MSE, then applies a bias correction from the statistics of minima to get an unbiased noise estimate — [Martin 2001, IEEE TSAP 9(5)](https://doi.org/10.1109/89.928915) (summary via [SciSpace](https://scispace.com/papers/noise-power-spectral-density-estimation-based-on-optimal-2dc3ftbbiq)). Minima are tracked "over a sliding temporal window … typically over a couple of seconds" — [search summary / Martin 2001](https://scispace.com/papers/noise-power-spectral-density-estimation-based-on-optimal-2dc3ftbbiq). (Typical window ≈1.5 s, split into U=8 sub-windows of V frames for efficient minimum search — from memory of the paper; not re-verified.)
- **MCRA (Cohen & Berdugo 2002):** "updates the noise estimate by tracking the noise-only regions of the noisy speech spectrum by comparing the ratio of the noisy speech to the local minimum against a threshold" — [Cohen & Berdugo 2002, IEEE SPL 9(1)](https://doi.org/10.1109/97.988717) (summary via [ResearchGate MS/MCRA for ASR](https://www.researchgate.net/publication/224641027_Application_of_Minimum_Statistics_and_Minima_Controlled_Recursive_Averaging_Methods_to_Estimate_a_Cepstral_Noise_Model_for_Robust_ASR)). Pseudocode (textbook form):
  ```
  S(k,l)   = αs·S(k,l-1) + (1-αs)·Σ_i w(i)|Y(k-i,l)|²      // αs≈0.8, freq smoothing over ±1 bin
  Smin     = min(Smin, S); every L frames (~0.5–1.5 s) reset Smin=min(Stmp,S), Stmp=S
  Sr       = S / Smin
  I(k,l)   = Sr > δ  (δ≈5)                                  // speech present indicator
  p(k,l)   = αp·p(k,l-1) + (1-αp)·I(k,l)                    // αp≈0.2
  α̃d      = αd + (1-αd)·p                                  // αd≈0.95
  λd(k,l+1)= α̃d·λd(k,l) + (1-α̃d)·|Y(k,l)|²
  ```
  (constants from memory of Cohen & Berdugo 2002 / Loizou ch.9; verify.)
- **MCRA-2 / Rangachari & Loizou 2006** ("A noise-estimation algorithm for highly non-stationary environments") uses continuous minimum tracking and frequency-dependent thresholds; Loizou's MATLAB code ships it as the default noise estimator for several gain functions (Wiener, MMSE, logMMSE, MMSE-SPU, pMMSE, SpecSub) — [UTD Loizou article PDF](https://ecs.utdallas.edu/loizou/speech/noise_estim_article_feb2006.pdf); [Loizou MATLAB software page](https://ecs.utdallas.edu/loizou/speech/software.htm)
- **IMCRA (Cohen 2003)** adds two smoothing/minimum-tracking iterations and a speech-presence-probability-based estimator; more robust but notably more code — [Cohen 2003, IEEE TSAP 11(5)](https://doi.org/10.1109/TSA.2003.811544)
- noisereduce non-stationary mode is effectively a crude adaptive estimator: an IIR-smoothed (time constant `time_constant_s=2.0` s) magnitude spectrogram is treated as the noise floor — [noisereduce nonstationary.py](https://github.com/timsainb/noisereduce)
- ffmpeg `afftdn` has `track_noise` (tn, default false) for automatic noise-floor tracking and `sample_noise` start/stop for profile capture — bundled `ffmpeg -h filter=afftdn` (7.0.2); [ffmpeg-filters docs](https://ffmpeg.org/ffmpeg-filters.html#afftdn)

### Inferences
- In this app the user presses "record" and there is typically ≥200 ms of room noise before speech → fixed-profile-from-leading-frames is realistic. Add an explicit "calibrate 1 s of silence" button in the UI for robustness.
- Minimum-statistics style estimators lag 1–2 s behind noise changes and over-estimate during long continuous speech if window too short; underestimate bias must be corrected (the hard part of Martin's paper). For a hackathon, a simplified "min over last 1.5 s of smoothed PSD × bias factor ≈1.5–2" is a pragmatic approximation.
- Risk with fixed profile: if the leading segment contains speech/breath, the whole recording is over-suppressed. Guard with an energy check.

### Gaps
- Could not fetch the Rangachari–Loizou 2006 PDF (TLS certificate error), so its exact parameter table (η, γ, β, α_p, δ per band) is not captured here.
- No source found comparing these estimators specifically for ASR front-ends in 2024–2026.

---

## Q3. Spectral gating as in Python `noisereduce` (Sainburg) — stationary vs non-stationary

### Takeaway
noisereduce is a (soft) binary-mask spectral gate, not an MMSE estimator: stationary mode thresholds each bin at mean+1.5·std (in dB) of the noise clip's spectrum; non-stationary mode compares each bin to a 2-s IIR-smoothed version of the signal itself through a sigmoid. Masks are smoothed in time/frequency (default 50 ms × 500 Hz) to suppress musical noise. Very easy to port to JS.

### Cited Findings
- Defaults: `n_std_thresh_stationary=1.5`, `prop_decrease=1.0`, `time_constant_s=2.0`, `freq_mask_smooth_hz=500`, `time_mask_smooth_ms=50`, `thresh_n_mult_nonstationary=1`, `sigmoid_slope_nonstationary=10`, `n_fft=1024`, `chunk_size=60000`, `padding=30000`, `n_jobs` for parallelism — [noisereduce README](https://github.com/timsainb/noisereduce)
- Stationary: "a noise clip containing prototypical noise of clip (optional)" + signal; fixed gate for whole signal — [README](https://github.com/timsainb/noisereduce). Non-stationary: "continuously updates the estimated noise threshold over time"; suited when signal events have known timescales and longer-duration energy is treated as background — [README](https://github.com/timsainb/noisereduce)
- **Stationary algorithm (from `spectralgate/stationary.py`)**:
  ```
  N = STFT(noise); N_db = amp_to_db(|N|)
  mean_f = mean(N_db, axis=time); std_f = std(N_db, axis=time)
  thresh_f = mean_f + n_std_thresh_stationary * std_f
  S = STFT(signal); mask = (amp_to_db(|S|) > thresh_f)            # binary
  mask = mask*prop_decrease + (1 - prop_decrease)
  mask = fftconvolve(mask, smoothing_filter, 'same')               # optional smoothing
  out  = iSTFT(S * mask)
  ```
  — [noisereduce stationary.py](https://github.com/timsainb/noisereduce/blob/master/noisereduce/spectralgate/stationary.py). If no noise clip is given the signal itself is used to compute the statistics (README: noise clip "optional").
- **Non-stationary algorithm (`spectralgate/nonstationary.py`)**:
  ```
  t_frames = time_constant_s * sr / hop_length
  b = (sqrt(1 + 4*t_frames^2) - 1) / (2*t_frames^2)
  S_smooth = filtfilt([b], [1, b-1], |S|, axis=time)               # zero-phase 1-pole IIR
  ratio = (|S| - S_smooth) / S_smooth
  mask  = sigmoid(ratio, -thresh_n_mult_nonstationary, sigmoid_slope_nonstationary)
  mask  = smooth(mask); mask = mask*prop_decrease + (1-prop_decrease)
  ```
  — [noisereduce nonstationary.py](https://github.com/timsainb/noisereduce/blob/master/noisereduce/spectralgate/nonstationary.py)
- Chunked processing: larger chunks → more memory, faster; padding must be large enough for the time constant to avoid edge artifacts — [README](https://github.com/timsainb/noisereduce)
- A PyTorch variant (`TorchGate`) exists in the same package for GPU — [README](https://github.com/timsainb/noisereduce)

### Inferences
- `filtfilt` is non-causal (forward+backward) → the non-stationary mode as written cannot run in real time; a causal port would use a forward-only one-pole IIR (introduces a lag bias) — acceptable for streaming.
- Stationary mode is causal once the noise statistics are known → trivially streamable (per-frame threshold compare + mask smoothing with a small look-ahead of a few frames for the 50 ms time smoothing).
- The mask smoothing kernel (500 Hz × 50 ms) is noisereduce's anti-musical-noise device, analogous to DD smoothing in log-MMSE. With 48 kHz / n_fft 1024 (46.9 Hz bins, hop 256 = 5.3 ms) that's ≈±5 bins × ±5 frames.
- prop_decrease <1 (e.g. 0.8) acts like a gain floor (−14 dB) — recommended for ASR use.

### Gaps
- Default `hop_length`/`win_length` not captured (README summary did not list them; believed to be n_fft/4 and n_fft).

---

## Q4. Existing implementations: ffmpeg filters, SoX noisered, JS FFT libs, npm packages

### Takeaway
The quickest zero-code path is ffmpeg (already bundled): `highpass=f=80-100` + `afftdn` (FFT spectral denoiser with noise-profile sampling and tracking) or `anlmdn` (non-local means). `arnndn` is RNNoise (a small neural net — not classical, and needs an external model file). For from-scratch JS, `fft.js` (MIT, radix-4, real-FFT helper) is the standard FFT; no maintained npm package for spectral subtraction/log-MMSE was found — port `logmmse.py` (~50 lines).

### Cited Findings
**ffmpeg 7.0.2 (bundled, from `ffmpeg -h filter=…`)**
- `afftdn` "Denoise audio samples using FFT": `nr` noise reduction 0.01–97 dB (default 12); `nf` noise floor −80…−20 dB (default −50); `nt` noise type white/vinyl/shellac/custom (default white); `bn` custom band noise; `rf` residual floor −80…−20 (default −38); `tn` track noise (default false); `tr` track residual; `om` output mode input/output/noise; `ad` adaptivity 0–1 (default 0.5); `fo` floor offset (default 1); `nl` noise link none/min/max/average (default min); `bm` band multiplier (default 1.25); `sn` sample_noise start/stop; `gs` gain smooth radius 0–50 (default 0). Supports runtime commands (`T` flag) and timeline `enable` — local binary help; [ffmpeg-filters afftdn](https://ffmpeg.org/ffmpeg-filters.html#afftdn). Doc example: `afftdn=nr=10:nf=-40:tn=1` — [ffmpeg-filters](https://ffmpeg.org/ffmpeg-filters.html)
- Noise-profile trick documented in ffmpeg docs: use `asendcmd` to send `sample_noise start/stop` over the silent lead-in, e.g. `asendcmd=c='0.0 afftdn sn start; 0.5 afftdn sn stop',afftdn` (pattern from ffmpeg docs examples; exact syntax should be checked in the Examples subsection) — [ffmpeg-filters afftdn Examples](https://ffmpeg.org/ffmpeg-filters.html#afftdn)
- `anlmdn` "Reduce broadband noise … using Non-Local Means": `s` strength 1e-5…10000 (default 1e-5, i.e. almost off — must raise, e.g. 0.001–0.01), `p` patch 2 ms, `r` research 6 ms, `m` smooth 11, output i/o/n — local binary help; [ffmpeg-filters anlmdn](https://ffmpeg.org/ffmpeg-filters.html#anlmdn)
- `arnndn` "Reduce noise from speech using Recurrent Neural Networks": `m` model file path (required; no default model built-in), `mix` −1…1 — local binary help. Models are the RNNoise-format `.rnnn` files (commonly from the GregorR/rnnoise-models repo) — [ffmpeg-filters arnndn](https://ffmpeg.org/ffmpeg-filters.html#arnndn)
- `afwtdn` wavelet denoiser: `sigma` 0–1 (default 0 = off), `levels` 10, `wavet` sym10, `percent` 85, `profile`, `adaptive`, `samples` 8192, `softness` 1 — local binary help
- `highpass`: default f=3000 Hz (!) — must set explicitly, e.g. `highpass=f=80`; 2 poles, Q 0.707 — local binary help. Low-pass: "smooths audio by attenuating treble" — [ffmpeg-filters](https://ffmpeg.org/ffmpeg-filters.html)
- ffmpeg also has `agate` (noise gate) and `speechnorm` — [ffmpeg-filters](https://ffmpeg.org/ffmpeg-filters.html)

**SoX**
- Two-step: `sox noise.wav -n noiseprof noise.prof` then `sox in.wav out.wav noisered noise.prof 0.21`; amount 0–1; "moderately effective at removing consistent background noise such as hiss or hum" — [sox(1) man page](https://linux.die.net/man/1/sox); users report 0.2–0.3 works best — [devoncrouse gist](https://gist.github.com/devoncrouse/5534261) / [dshanpi wiki](https://wiki.dshanpi.org/en/blog/sox-rnnoise-denoise/)
- Algorithm internals are poorly documented; a sox-users thread asking "what exactly Sox's Noisered algorithm is doing" received no documented answer — [SoX mailing list 2017](https://sourceforge.net/p/sox/mailman/message/36016022/). It is described by third parties as spectral subtraction on the amplitude spectrum keeping original phase — [dshanpi wiki](https://wiki.dshanpi.org/en/blog/sox-rnnoise-denoise/). SoX is not bundled in this project (would need a separate binary).

**JS / npm**
- `fft.js` (Fedor Indutny): "fastest JS Radix-4/Radix-2 FFT"; size must be power of 2; interleaved complex arrays; `createComplexArray()`, `realTransform(out,in)` (~25 % faster for real input), `completeSpectrum()`, `transform`, `inverseTransform`; benchmark ≈35k ops/s at N=2048; MIT — [indutny/fft.js](https://github.com/indutny/fft.js)
- Browser-built-in alternative: `getUserMedia({audio:{noiseSuppression:true}})` (WebRTC NS); constraint is "not Baseline"; check `getSupportedConstraints().noiseSuppression` — [MDN noiseSuppression](https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackConstraints/noiseSuppression)
- Example PoC comparing Original / High-Pass / Noise Gate / RNNoise / Browser NS / Spectral Subtraction on browser mic PCM: [varakumar-divami/noise-poc](https://github.com/varakumar-divami/noise-poc); an experimental spectral-subtraction repo: [Sarmkadan/spectral-denoise](https://github.com/Sarmkadan/spectral-denoise) (WIP, maturity unknown)
- DL option for Node (out of scope but relevant): `workadventure/noise-suppression` — DTLN with prebuilt binaries for Node, and an AudioWorklet build — [GitHub](https://github.com/workadventure/noise-suppression)

**Python references**
- `logmmse` (PyPI; port of Loizou-style logmmse.m) — [GitHub](https://github.com/wilsonchingg/logmmse); MMSE-STSA reference — [eesungkim](https://github.com/eesungkim/Speech_Enhancement_MMSE-STSA); Loizou MATLAB (specsub, mband, wiener_as, mt_mmse, logmmse, logmmse_SPU, MCRA2 noise est.) — [UTD software page](https://ecs.utdallas.edu/loizou/speech/software.htm); a repository mirroring Loizou's MATLAB code — [jtkim-kaist/Speech-enhancement](https://github.com/jtkim-kaist/Speech-enhancement); objective metrics from Loizou's book (PESQ, segSNR, LLR, WSS…) — [pysepm](https://github.com/schmiph2/pysepm); VOICEBOX `ssubmmse.m` — [mirror](https://github.com/YouriT/matlab-speech/blob/master/MATLAB_CODE_SOURCE/voicebox/ssubmmse.m)

### Inferences
- Offline recordings in this repo (`recordings/`) can be denoised server-side with one `spawn(ffmpegPath, ['-i',in,'-af','highpass=f=80,afftdn=nr=12:nf=-40:tn=1','-ar','16000',out])` — zero algorithm code; use `om=n` to listen to what was removed (good demo).
- For a "we built it ourselves" hackathon story, a JS log-MMSE/Wiener-DD using fft.js is ≈150 lines incl. framing/OLA.

### Gaps
- No actively maintained npm package implementing spectral subtraction / Wiener / log-MMSE was found (searches returned only experimental GitHub repos).
- afftdn's internal algorithm (it is a port of a Wiener-style FFT denoiser with band-wise noise profile) was not verified from source this session.

---

## Q5. Real-time considerations: latency, overlap-add, AudioWorklet vs Node

### Takeaway
Algorithmic latency of an STFT denoiser = one frame (window length) plus buffering; 20–32 ms frames at 50 % overlap give ~25–45 ms, negligible versus network/ASR latency. AudioWorklet delivers 128-sample render quanta, so you need input/output ring buffers to assemble FFT frames; Node-side processing of the WebSocket PCM chunks is simpler (no realtime-thread constraints) and keeps the raw audio available.

### Cited Findings
- Browsers call `AudioWorkletProcessor.process()` with 128 frames per render quantum; Chrome's AudioWorklet design guidance recommends ring buffers when the algorithm needs a different block size — [WorkAdventure blog](https://workadventu.re/tech/building-an-easy-to-use-browser-noise-suppression-library-in-an-audio-worklet/)
- Worked example: DTLN processor buffers four 128-sample quanta into one 512-sample frame and writes denoised samples to an output ring buffer, creating "a small and intentional startup delay" — [WorkAdventure blog](https://workadventu.re/tech/building-an-easy-to-use-browser-noise-suppression-library-in-an-audio-worklet/)
- Overlap-add synthesis with overlapping analysis windows is used "for masking discontinuities" in noise suppressors — [search summary of US patent literature](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/6810273)
- `logmmse` supports streaming by returning/accepting `saved_params` = {`noise_mu2`, `Xk_prev`, `x_old`} (noise PSD, previous clean power for DD, overlap tail) — i.e. exactly the state needed for chunked real-time processing — [logmmse source](https://github.com/wilsonchingg/logmmse)
- fft.js N=2048 ≈35k transforms/s → at 48 kHz, hop 512 needs ~94 FFT+iFFT pairs/s, i.e. <1 % of one core — [fft.js benchmarks](https://github.com/indutny/fft.js) (inference on the throughput)

### Inferences
- Streaming OLA pseudocode (per hop H, window N, with √Hann analysis & synthesis at 50 % → COLA):
  ```
  on new samples: inBuf.push(samples)
  while inBuf.length >= N:
      frame = inBuf[0:N] * win
      Y = rfft(frame); P = |Y|^2
      update noise λd (VAD/MCRA); compute ξ (DD), G = gain(ξ,γ); G = max(G, Gmin)
      y = irfft(G*Y) * win
      outAcc[0:N] += y; emit outAcc[0:H]; shift outAcc by H; inBuf shift by H
  ```
  Latency = N samples (+ H of output alignment). N=1024@48k → 21 ms.
- AudioWorklet caveats: no allocation in `process()` (preallocate Float32Arrays), keep work per quantum ≲ 2.67 ms at 48 kHz, fft.js can be bundled into the worklet module. Denoising in the browser means the server/ASR receives only processed audio — cannot A/B later.
- Node-side (recommended here): WebSocket already delivers Int16 PCM chunks → convert to Float32 (÷32768), run the same streaming class, convert back (clip, ×32767) before forwarding to Google STT. Keep the raw stream too, so denoising can be toggled per session and WER compared.
- Python `noisereduce` non-stationary (`filtfilt`) and whole-file stationary stats are offline only; use for the recording-file path, not the live path.

### Gaps
- No measured latency/CPU numbers for a JS log-MMSE implementation were found; the throughput estimate above is derived from fft.js benchmark only.

---

## Q6. Effect of such denoising on ASR accuracy (Google STT / modern ASR)

### Takeaway
Strong, consistent evidence that front-end denoising usually HURTS modern end-to-end ASR: Google explicitly says to disable noise reduction before sending audio to Speech-to-Text, and a Dec-2025 study found enhancement increased error in all 40 tested configurations (Whisper, Parakeet, Gemini Flash 2.0, Parrotlet). Denoising should be optional, mild (gain floor), and primarily for human listening/playback — or evaluated A/B on your own data.

### Cited Findings
- Google Cloud Speech-to-Text best practices: "applying noise-reduction signal processing to the audio before sending it to the service typically reduces recognition accuracy"; "All noise reduction processing should be disabled"; "Do not use automatic gain control (AGC)"; "If possible, set the sampling rate of the audio source to 16000 Hz" — [Google Cloud STT best practices](https://docs.cloud.google.com/speech-to-text/docs/best-practices)
- Chondhekar et al. (arXiv 2512.17562, Dec 19 2025) "When De-noising Hurts": MetricGAN+ (VoiceBank) enhancement on 500 medical recordings, 9 noise conditions, 4 ASR systems (Whisper, NVIDIA Parakeet, Gemini Flash 2.0, Parrotlet): original noisy audio achieved lower semWER in all 40 configurations; degradation 1.1 %–46.6 % absolute semWER — [arXiv 2512.17562](https://arxiv.org/abs/2512.17562). Proposed reasons: modern ASR is internally noise-robust; enhancement removes acoustic cues and introduces distribution shift, effect larger for bigger models — [arXiv PDF](https://arxiv.org/pdf/2512.17562)
- arXiv 2603.04710 (2026) "When Audio Separation Hurts Zero-Shot ASR": SAM-Audio separation before Whisper on Bengali and English — every configuration across five Whisper sizes and two languages had higher WER/CER after processing even though PSNR rose — [arXiv 2603.04710](https://arxiv.org/html/2603.04710)
- Note: both studies use neural enhancers, not classical DSP; the Google guidance covers noise reduction generally.

### Inferences
- Classical spectral subtraction/Wiener produce artifacts (musical noise, spectral holes, attenuated low-SNR consonants like /s/ /f/) that are out-of-distribution for ASR trained on large noisy corpora → expect the same or worse direction of effect as neural enhancers; mild settings (G_min −10…−15 dB, prop_decrease 0.5–0.8, afftdn nr 6–12) limit harm.
- Low-risk operations that are generally safe for ASR: DC removal / high-pass at 60–100 Hz (removes rumble/hum fundamentals below speech F0), correct level (no clipping). Everything else: measure.
- Suggested product design: send RAW audio to Google STT; apply classical denoising to the saved recording / playback (human-facing) path, and optionally offer a toggle with an A/B WER check on a few team-recorded clips (Japanese). Possible exception worth testing: strong stationary noise (fan/AC hum) at very low SNR, where mild stationary gating sometimes helps — no source found confirming this for Chirp 3.

### Gaps
- No study found that measures classical (spectral subtraction / Wiener / log-MMSE) pre-processing on Google Chirp/Chirp 3 or on Japanese ASR specifically; older (pre-2018 HMM/DNN-hybrid era) literature reported gains from spectral subtraction front-ends, but that likely does not transfer to modern end-to-end models.
