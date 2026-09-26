'use strict';

// 発話ごとの「声の出し方」を測る。ブラウザの AnalyserNode から 50ms ごとに
// 音量 (dB) とスペクトル (dB) を受け取り、発話の終わりに要約を返す。
//
// - 声の大きさ: 声が出ているフレームの平均 dB / 最大 dB
// - 伸ばし: 音量も音色 (スペクトル) もほとんど変わらない音が続いた最長の時間。
//   「さーーーっと」の「ーーー」のように母音を伸ばすと長くなる。普通にしゃべると
//   子音で音色が次々に変わるので短く切れる。
//
// 判定の基準はセッション全体の平均から決めるので、マイクや話し方の個人差に左右されにくい。

class ProsodyMeter {
  constructor(frameMs) {
    this.frameMs = frameMs;
    this.fluxAvg = null; // セッション全体での、声のフレーム間の音色変化の平均
    this.reset();
  }

  // 発話 (チャンク) ごとにリセットする。fluxAvg はセッションを通して持ち越す。
  reset() {
    this.dbs = [];
    this.prevDb = null;
    this.prevSpec = null;
    this.run = 0; // 今続いている「伸ばし」のフレーム数
    this.miss = 0; // 伸ばし中に基準を外れた連続フレーム数 (1 フレームだけなら許す)
    this.maxRun = 0;
  }

  // db: このフレームの音量 (dBFS)、spec: スペクトル (dB, Float32Array)、voiced: 声か
  push(db, spec, voiced) {
    if (!voiced) {
      this._endRun();
      this.prevDb = null;
      this.prevSpec = null;
      return;
    }
    this.dbs.push(db);

    if (this.prevSpec) {
      const flux = spectralFlux(this.prevSpec, spec);
      this.fluxAvg = this.fluxAvg === null ? flux : this.fluxAvg * 0.98 + flux * 0.02;
      const steady = Math.abs(db - this.prevDb) < 3 && flux < this.fluxAvg * 0.7;
      if (steady) {
        this.run += 1 + this.miss;
        this.miss = 0;
        this.maxRun = Math.max(this.maxRun, this.run);
      } else if (this.run > 0 && this.miss === 0) {
        this.miss = 1;
      } else {
        this._endRun();
      }
    }
    this.prevDb = db;
    this.prevSpec = spec.slice();
  }

  _endRun() {
    this.run = 0;
    this.miss = 0;
  }

  // 発話の要約。声のフレームがなければ null
  summary() {
    if (!this.dbs.length) return null;
    const mean = this.dbs.reduce((a, b) => a + b, 0) / this.dbs.length;
    const round1 = (v) => Math.round(v * 10) / 10;
    return {
      speechSec: round1((this.dbs.length * this.frameMs) / 1000),
      meanDb: round1(mean),
      peakDb: round1(Math.max(...this.dbs)),
      // 伸ばしは連続フレームの「間」の数で数えるので +1 フレーム分
      sustainSec: round1(this.maxRun ? ((this.maxRun + 1) * this.frameMs) / 1000 : 0),
    };
  }
}

// 声の帯域 (約 70Hz〜4kHz) で、隣り合うフレームのスペクトルの変化量 (dB の平均絶対差)
function spectralFlux(a, b) {
  const lo = 3;
  const hi = Math.min(a.length, 175);
  let sum = 0;
  for (let i = lo; i < hi; i++) {
    const x = Number.isFinite(a[i]) ? a[i] : -140;
    const y = Number.isFinite(b[i]) ? b[i] : -140;
    sum += Math.abs(x - y);
  }
  return sum / (hi - lo);
}

// ---- 発話ごとの声の特徴を、セッション内の「普段」と比べた言葉にする (画面とサーバーで共通) ----

function median(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

// 句読点や記号を除いた文字数 (話す速さの目安)
function charCount(text) {
  return (text.match(/[\p{L}\p{N}ー]/gu) || []).length;
}

// segments: [{ text, voice: { speechSec, meanDb, peakDb, sustainSec } | undefined }]
// 戻り値: segments と同じ順で、{ tags: ['大きめ(+4dB)', ...], sustainSec, baselineSustainSec } | null
function describeVoice(segments) {
  const withVoice = segments.filter((s) => s.voice && s.voice.speechSec > 0);
  const hasBaseline = withVoice.length >= 3;
  const medDb = median(withVoice.map((s) => s.voice.meanDb));
  const medEmph = median(withVoice.map((s) => s.voice.peakDb - s.voice.meanDb));
  const medSustain = median(withVoice.map((s) => s.voice.sustainSec));
  const medRate = median(withVoice.map((s) => charCount(s.text) / s.voice.speechSec));

  return segments.map((s) => {
    const v = s.voice;
    if (!v || !v.speechSec) return null;
    const tags = [];
    if (hasBaseline) {
      const d = v.meanDb - medDb;
      if (d >= 3) tags.push(`大きめ(+${d.toFixed(0)}dB)`);
      else if (d <= -8) tags.push(`ささやき気味(${d.toFixed(0)}dB)`);
      else if (d <= -3) tags.push(`小さめ(${d.toFixed(0)}dB)`);
      if (v.peakDb - v.meanDb - medEmph >= 4) tags.push('一部を強く言った');
      const rate = charCount(s.text) / v.speechSec;
      if (rate <= medRate * 0.75) tags.push('ゆっくり');
      else if (rate >= medRate * 1.3) tags.push('早口');
    }
    const longSustain = v.sustainSec >= 0.5 && (!hasBaseline || v.sustainSec >= medSustain * 1.8);
    if (longSustain) {
      tags.push(hasBaseline
        ? `音を${v.sustainSec}秒伸ばした(普段${medSustain.toFixed(1)}秒)`
        : `音を${v.sustainSec}秒伸ばした`);
    }
    return { tags, sustainSec: v.sustainSec, baselineSustainSec: medSustain };
  });
}

if (typeof module !== 'undefined') module.exports = { ProsodyMeter, spectralFlux, describeVoice };
