'use strict';

const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const statusEl = document.getElementById('status');
const finalTextEl = document.getElementById('finalText');
const partialTextEl = document.getElementById('partialText');
const jsonOutputEl = document.getElementById('jsonOutput');
const downloadBtn = document.getElementById('downloadBtn');
const audioDownloadEl = document.getElementById('audioDownload');
const analyzeDnnEl = document.getElementById('analyzeDnn');
const analysisAreaEl = document.getElementById('analysisArea');
const analysisStatusEl = document.getElementById('analysisStatus');
const analysisBodyEl = document.getElementById('analysisBody');
const metricsEl = document.getElementById('metrics');
const reportLinkEl = document.getElementById('reportLink');

let ws = null;
let audioContext = null;
let sourceNode = null;
let processorNode = null;
let mediaStream = null;
let lastSession = null;

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
  analysisAreaEl.hidden = true;
  analysisBodyEl.hidden = true;
  audioDownloadEl.classList.add('is-disabled');
  audioDownloadEl.removeAttribute('download');
  audioDownloadEl.href = '#';

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
      lastSession = msg.session;
      jsonOutputEl.textContent = JSON.stringify(lastSession, null, 2);
      downloadBtn.disabled = false;
      if (lastSession.audioUrl) {
        audioDownloadEl.href = lastSession.audioUrl;
        audioDownloadEl.download = `recording_${lastSession.sessionId}.mp4`;
        audioDownloadEl.classList.remove('is-disabled');
      }
      finishUI();
      // 解析が続く場合は結果を受け取るまで接続を保つ
      if (!msg.analysisPending && ws) ws.close();
      break;
    case 'analysis_started':
      analysisAreaEl.hidden = false;
      analysisBodyEl.hidden = true;
      reportLinkEl.classList.add('is-disabled');
      analysisStatusEl.textContent = msg.dnn
        ? '解析中… (深層学習で分離するため 1 分ほどかかることがあります)'
        : '解析中… (数秒かかります)';
      break;
    case 'analysis':
      showAnalysis(msg.analysis);
      if (ws) ws.close();
      break;
    case 'analysis_error':
      analysisAreaEl.hidden = false;
      analysisStatusEl.textContent = `解析できませんでした: ${msg.message}`;
      if (ws) ws.close();
      break;
    case 'error':
      setStatus(`エラー: ${msg.message}`);
      break;
    default:
      break;
  }
}

function fmt(v, digits, unit) {
  return typeof v === 'number' && isFinite(v) ? `${v.toFixed(digits)}${unit}` : '—';
}

function showAnalysis(a) {
  const m = a.metrics;
  const cards = [
    ['大きさ', fmt(m.loudnessLufs, 1, ' LUFS'), `ピーク ${fmt(m.peakDbfs, 1, ' dBFS')}`],
    ['声の高さ', fmt(m.f0MedianHz, 0, ' Hz'), m.note ? `中央値 (${m.note})` : '中央値'],
    ['抑揚', fmt(m.f0RangeSemitones, 1, ' 半音'), '高さの幅 (5〜95%)'],
    [
      '話す速さ',
      typeof m.moraRate === 'number' ? fmt(m.moraRate, 1, ' モーラ/秒') : fmt(m.syllableRate, 1, ' 音節/秒'),
      typeof m.moraRate === 'number'
        ? `間を除くと ${fmt(m.articulationMoraRate, 1, '')} / ${m.morae} モーラ`
        : '文字起こしなし (音節数から推定)',
    ],
    ['ポーズ', typeof m.pauses === 'number' ? `${m.pauses} 回` : '—', '0.3 秒以上の間'],
    ['環境音', fmt(m.environmentLufs, 1, ' LUFS'), '声を除いた残り'],
  ];
  metricsEl.innerHTML = '';
  for (const [label, value, sub] of cards) {
    const div = document.createElement('div');
    div.className = 'metric';
    div.innerHTML = '<span class="metric-label"></span><span class="metric-value"></span><span class="metric-sub"></span>';
    div.children[0].textContent = label;
    div.children[1].textContent = value;
    div.children[2].textContent = sub;
    metricsEl.appendChild(div);
  }
  const bust = `?t=${Date.now()}`;
  document.getElementById('audioOriginal').src = a.audio.original;
  document.getElementById('audioSpeech').src = a.audio.speech + bust;
  document.getElementById('audioEnvironment').src = a.audio.environment + bust;
  document.getElementById('figProsody').src = a.figures.prosody + bust;
  document.getElementById('figSeparation').src = a.figures.separation + bust;
  document.getElementById('figSpectrogram').src = a.figures.spectrogram + bust;
  document.getElementById('figSpectrum').src = a.figures.spectrum + bust;
  document.getElementById('methodNote').textContent = `分離の方式: ${a.method}`;
  reportLinkEl.href = a.reportUrl;
  reportLinkEl.classList.remove('is-disabled');
  analysisStatusEl.textContent = '';
  analysisAreaEl.hidden = false;
  analysisBodyEl.hidden = false;
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
