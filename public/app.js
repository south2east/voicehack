'use strict';

const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const statusEl = document.getElementById('status');
const finalTextEl = document.getElementById('finalText');
const partialTextEl = document.getElementById('partialText');
const jsonOutputEl = document.getElementById('jsonOutput');
const downloadBtn = document.getElementById('downloadBtn');
const audioDownloadEl = document.getElementById('audioDownload');
const analysisStatusEl = document.getElementById('analysisStatus');
const analysisMetricsEl = document.getElementById('analysisMetrics');
const analysisImagesEl = document.getElementById('analysisImages');
const analysisPlayersEl = document.getElementById('analysisPlayers');
const analyzeDnnEl = document.getElementById('analyzeDnn');

const ANALYSIS_POLL_MS = 2000;

let ws = null;
let audioContext = null;
let sourceNode = null;
let processorNode = null;
let mediaStream = null;
let lastSession = null;
let analysisTimer = null;

function setStatus(text, recording) {
  statusEl.textContent = text;
  statusEl.classList.toggle('recording', !!recording);
}

// Float32 [-1, 1] のPCMをLINEAR16 (Int16)に変換
function floatTo16BitPCM(float32Array) {
  const buffer = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buffer);
  let offset = 0;
  for (let i = 0; i < float32Array.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

async function start() {
  startBtn.disabled = true;
  setStatus('マイクにアクセス中…');

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setStatus('マイクへのアクセスが拒否されました');
    startBtn.disabled = false;
    return;
  }

  audioContext = new (window.AudioContext || window.webkitAudioContext)();
  const sampleRate = audioContext.sampleRate;

  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${protocol}://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    ws.send(
      JSON.stringify({
        type: 'start',
        sampleRate,
        languageCode: 'ja-JP',
        analyzeDnn: analyzeDnnEl.checked,
      })
    );
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    handleServerMessage(msg);
  };

  ws.onerror = () => {
    setStatus('接続エラー');
  };

  ws.onclose = () => {
    if (statusEl.textContent !== '停止しました') {
      setStatus('接続が切断されました');
    }
  };

  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  // ScriptProcessorNode は非推奨だが、シンプルさのためにここでは使用
  processorNode = audioContext.createScriptProcessor(4096, 1, 1);

  processorNode.onaudioprocess = (event) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const input = event.inputBuffer.getChannelData(0);
    const pcm16 = floatTo16BitPCM(input);
    ws.send(pcm16);
  };

  sourceNode.connect(processorNode);
  processorNode.connect(audioContext.destination);

  finalTextEl.textContent = '';
  partialTextEl.textContent = '';
  jsonOutputEl.textContent = '記録中…';
  downloadBtn.disabled = true;
  audioDownloadEl.classList.add('is-disabled');
  audioDownloadEl.removeAttribute('download');
  audioDownloadEl.href = '#';
  resetAnalysis();

  setStatus('録音中… (話しかけてください)', true);
  stopBtn.disabled = false;
}

function stop() {
  stopBtn.disabled = true;
  setStatus('停止処理中…');

  if (processorNode) {
    processorNode.disconnect();
    processorNode.onaudioprocess = null;
    processorNode = null;
  }
  if (sourceNode) {
    sourceNode.disconnect();
    sourceNode = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'stop' }));
  } else {
    finishUI();
  }
}

function handleServerMessage(msg) {
  switch (msg.type) {
    case 'started':
      break;
    case 'partial':
      partialTextEl.textContent = msg.transcript;
      break;
    case 'final':
      partialTextEl.textContent = '';
      finalTextEl.textContent += msg.transcript;
      break;
    case 'stopped':
      showSession(msg.session);
      downloadBtn.disabled = false;
      if (lastSession.audioUrl) {
        audioDownloadEl.href = lastSession.audioUrl;
        audioDownloadEl.download = `recording_${lastSession.sessionId}.mp4`;
        audioDownloadEl.classList.remove('is-disabled');
      }
      renderAnalysis(lastSession.analysis);
      if (lastSession.analysis && lastSession.analysis.status === 'running') {
        pollAnalysis(lastSession.sessionId);
      }
      finishUI();
      if (ws) ws.close();
      break;
    case 'error':
      setStatus(`エラー: ${msg.message}`);
      break;
    default:
      break;
  }
}

function showSession(session) {
  lastSession = session;
  jsonOutputEl.textContent = JSON.stringify(session, null, 2);
}

// 解析はサーバー側で録音終了後に走るので、終わるまで定期的に問い合わせる
function pollAnalysis(sessionId) {
  clearTimeout(analysisTimer);
  analysisTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/sessions/${sessionId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const session = await res.json();
      if (!lastSession || lastSession.sessionId !== sessionId) return;
      showSession(session);
      renderAnalysis(session.analysis);
      if (session.analysis && session.analysis.status === 'running') pollAnalysis(sessionId);
    } catch (err) {
      analysisStatusEl.textContent = `解析状況を取得できません: ${err.message}`;
    }
  }, ANALYSIS_POLL_MS);
}

function resetAnalysis() {
  clearTimeout(analysisTimer);
  analysisStatusEl.textContent = '終了すると録音を解析します';
  analysisMetricsEl.replaceChildren();
  analysisImagesEl.replaceChildren();
  analysisPlayersEl.replaceChildren();
}

function fmt(v, digits = 1, unit = '') {
  return v === null || v === undefined ? '—' : `${Number(v).toFixed(digits)}${unit}`;
}

function renderAnalysis(analysis) {
  analysisMetricsEl.replaceChildren();
  analysisImagesEl.replaceChildren();
  analysisPlayersEl.replaceChildren();
  if (!analysis) return;

  switch (analysis.status) {
    case 'running':
      analysisStatusEl.textContent = analysis.dnn
        ? '解析中… (深層学習で分離するため、録音の長さと同じくらいかかります)'
        : '解析中… (数秒かかります)';
      return;
    case 'failed':
      analysisStatusEl.textContent = `解析に失敗しました: ${analysis.error}`;
      return;
    case 'skipped':
      analysisStatusEl.textContent = `解析をスキップしました: ${analysis.error}`;
      return;
    case 'disabled':
      analysisStatusEl.textContent = '解析は無効です (ANALYSIS_ENABLED=false)';
      return;
    case 'done':
      break;
    default:
      return;
  }

  const r = analysis.report;
  analysisStatusEl.textContent = `解析完了 (${fmt(r.file.duration_s, 1, ' 秒')})`;
  const p = r.pitch;
  const rows = [
    ['大きさ', `${fmt(r.loudness.integrated_lufs, 1, ' LUFS')} / ピーク ${fmt(r.level.peak_dbfs, 1, ' dBFS')}`],
    ['トーン', p.median_f0_hz
      ? `F0 中央値 ${fmt(p.median_f0_hz, 0, ' Hz')} (${p.note}) / 抑揚 ${fmt(p.f0_range_semitones, 1, ' 半音')}`
      : '有声区間なし'],
    speedRow(r.rate),
  ];
  if (r.separation) {
    rows.push(['環境音', `${fmt(r.separation.environment.integrated_lufs, 1, ' LUFS')} (RMS ${fmt(r.separation.environment.rms_dbfs, 1, ' dBFS')})`]);
  }
  for (const [label, value] of rows) {
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.textContent = value;
    analysisMetricsEl.append(dt, dd);
  }

  // 元の音 / 声だけ / 環境音だけ の聞き比べ
  const players = [
    ['元の音', analysis.audio && analysis.audio.original],
    ['声だけ', analysis.audio && analysis.audio.speech],
    ['環境音だけ', analysis.audio && analysis.audio.environment],
  ];
  for (const [label, src] of players) {
    if (!src) continue;
    const row = document.createElement('div');
    row.className = 'player';
    const span = document.createElement('span');
    span.textContent = label;
    const audio = document.createElement('audio');
    audio.controls = true;
    audio.preload = 'none';
    audio.src = src;
    row.append(span, audio);
    analysisPlayersEl.append(row);
  }

  for (const src of analysis.images || []) {
    const a = document.createElement('a');
    a.href = src;
    a.target = '_blank';
    const img = document.createElement('img');
    img.src = src;
    img.alt = src.split('/').pop();
    img.loading = 'lazy';
    a.append(img);
    analysisImagesEl.append(a);
  }
  const link = document.createElement('a');
  link.href = analysis.reportUrl;
  link.target = '_blank';
  link.textContent = 'report.json を開く';
  analysisImagesEl.append(link);
}

// 文字起こしがあればモーラ/秒 (正確), 無ければ音節核から推定した音節/秒
function speedRow(rate) {
  const t = rate.transcript;
  if (t && t.morae) {
    return ['スピード', `発話速度 ${fmt(t.speech_rate_mora_per_s, 2, ' モーラ/s')} / 間を除くと ${fmt(t.articulation_rate_mora_per_s, 2, ' モーラ/s')} (${t.morae} モーラ) / ポーズ ${rate.n_pauses} 回`];
  }
  return ['スピード', `発話速度 ${fmt(rate.speech_rate_syll_per_s, 2, ' 音節/s')} / 調音速度 ${fmt(rate.articulation_rate_syll_per_s, 2, ' 音節/s')} / ポーズ ${rate.n_pauses} 回 (文字起こしなし)`];
}

function finishUI() {
  setStatus('停止しました', false);
  startBtn.disabled = false;
  stopBtn.disabled = true;
}

downloadBtn.addEventListener('click', () => {
  if (!lastSession) return;
  const blob = new Blob([JSON.stringify(lastSession, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `transcript_${lastSession.sessionId}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

startBtn.addEventListener('click', start);
stopBtn.addEventListener('click', stop);
