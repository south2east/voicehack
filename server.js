'use strict';

require('dotenv').config();

const path = require('path');
const fs = require('fs');
const http = require('http');
const crypto = require('crypto');
const { spawn } = require('child_process');
const express = require('express');
const { WebSocketServer } = require('ws');
const speech = require('@google-cloud/speech').v2;
const ffmpegPath = require('ffmpeg-static');

const PORT = process.env.PORT || 3000;
const PROJECT_ID = process.env.GOOGLE_CLOUD_PROJECT;
const LOCATION = process.env.GOOGLE_CLOUD_LOCATION || 'us-central1';
const MODEL = process.env.SPEECH_MODEL || 'chirp_3';
const DEFAULT_LANGUAGE = process.env.SPEECH_LANGUAGE || 'ja-JP';
const TRANSCRIPTS_DIR = path.join(__dirname, 'transcripts');
const RECORDINGS_DIR = path.join(__dirname, 'recordings');

for (const dir of [TRANSCRIPTS_DIR, RECORDINGS_DIR]) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

if (!PROJECT_ID) {
  console.warn(
    '[warn] GOOGLE_CLOUD_PROJECT が .env に設定されていません。Speech-to-Text の呼び出しは失敗します。'
  );
}

const app = express();
app.use(express.static(path.join(__dirname, 'public')));
app.use('/recordings', express.static(RECORDINGS_DIR));

const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

// Google の streamingRecognize は 1 ストリームあたり最大 ~5分の制約があるため、
// 上限に達する前にストリームを作り直して継続させる
const STREAM_RESTART_MS = 4 * 60 * 1000;

function buildRecognizerPath() {
  return `projects/${PROJECT_ID}/locations/${LOCATION}/recognizers/_`;
}

function buildStreamingConfig(sampleRateHertz, languageCode) {
  return {
    config: {
      explicitDecodingConfig: {
        encoding: 'LINEAR16',
        sampleRateHertz,
        audioChannelCount: 1,
      },
      model: MODEL,
      languageCodes: [languageCode || DEFAULT_LANGUAGE],
      features: {
        enableAutomaticPunctuation: true,
      },
    },
    streamingFeatures: {
      interimResults: true,
    },
  };
}

class TranscriptionSession {
  constructor(ws, { sampleRateHertz, languageCode }) {
    this.ws = ws;
    this.sampleRateHertz = sampleRateHertz;
    this.languageCode = languageCode || DEFAULT_LANGUAGE;
    this.id = crypto.randomUUID();
    this.startedAt = new Date().toISOString();
    this.endedAt = null;
    this.segments = []; // { text, isFinal, confidence, receivedAt }
    this.client = new speech.SpeechClient(
      LOCATION === 'global' ? {} : { apiEndpoint: `${LOCATION}-speech.googleapis.com` }
    );
    this.geminiStream = null;
    this.restartTimer = null;
    this.closed = false;
    this.pendingAudio = [];
    this.ffmpegProcess = null;
    this.audioFileName = null;
    this.recordingFailed = false;
  }

  start() {
    this._openStream();
    this._startRecording();
  }

  // 開始〜終了の間に受け取った生音声(PCM16)を、ffmpeg-static (npm同梱バイナリ) で
  // そのままMP4(AAC)へエンコードしながら保存する。ユーザー側でffmpegを別途
  // インストールする必要はない。
  _startRecording() {
    if (!ffmpegPath) {
      console.warn(`[session ${this.id}] ffmpeg-static のバイナリが見つからず、録音保存をスキップします`);
      this.recordingFailed = true;
      return;
    }
    const filename = `${this.startedAt.replace(/[:.]/g, '-')}_${this.id}.mp4`;
    const outputPath = path.join(RECORDINGS_DIR, filename);

    const proc = spawn(ffmpegPath, [
      '-hide_banner',
      '-loglevel', 'error',
      '-f', 's16le',
      '-ar', String(this.sampleRateHertz),
      '-ac', '1',
      '-i', 'pipe:0',
      '-c:a', 'aac',
      '-b:a', '128k',
      '-movflags', '+faststart',
      '-y',
      outputPath,
    ]);

    proc.stdin.on('error', () => {
      /* stdinへの書き込みエラーはprocess終了時によく起きるため無視 */
    });
    proc.stderr.on('data', (d) => {
      console.error(`[session ${this.id}] ffmpeg:`, d.toString().trim());
    });
    proc.on('error', (err) => {
      console.error(`[session ${this.id}] ffmpeg spawn error:`, err.message);
      this.recordingFailed = true;
    });

    this.ffmpegProcess = proc;
    this.audioFileName = filename;
  }

  _openStream() {
    if (this.closed) return;

    const recognizeStream = this.client._streamingRecognize();
    this.geminiStream = recognizeStream;

    recognizeStream.on('error', (err) => {
      console.error(`[session ${this.id}] streaming error:`, err.message);
      this._safeSend({ type: 'error', message: err.message });
    });

    recognizeStream.on('data', (response) => {
      this._handleResponse(response);
    });

    recognizeStream.on('end', () => {
      // ストリーム終了。closed でなければ再接続を試みる想定だが、
      // 通常は _restartStream / stop 経由で明示的にハンドルされる
    });

    recognizeStream.write({
      recognizer: buildRecognizerPath(),
      streamingConfig: buildStreamingConfig(this.sampleRateHertz, this.languageCode),
    });

    this.restartTimer = setTimeout(() => this._restartStream(), STREAM_RESTART_MS);
  }

  _restartStream() {
    if (this.closed) return;
    const old = this.geminiStream;
    this.geminiStream = null;
    this._openStream();
    if (old) {
      try {
        old.end();
      } catch (_) {
        /* noop */
      }
    }
  }

  _handleResponse(response) {
    const results = response.results || [];
    for (const result of results) {
      const alt = result.alternatives && result.alternatives[0];
      if (!alt) continue;
      const transcript = alt.transcript || '';
      const isFinal = !!result.isFinal;
      const payload = {
        type: isFinal ? 'final' : 'partial',
        transcript,
        confidence: alt.confidence ?? null,
      };
      if (isFinal) {
        this.segments.push({
          text: transcript,
          confidence: alt.confidence ?? null,
          receivedAt: new Date().toISOString(),
        });
      }
      this._safeSend(payload);
    }
  }

  writeAudio(chunk) {
    if (this.closed) return;
    if (this.geminiStream) {
      try {
        this.geminiStream.write({ audio: chunk });
      } catch (err) {
        console.error(`[session ${this.id}] write error:`, err.message);
      }
    }
    if (this.ffmpegProcess && this.ffmpegProcess.stdin.writable) {
      try {
        this.ffmpegProcess.stdin.write(chunk);
      } catch (err) {
        console.error(`[session ${this.id}] ffmpeg write error:`, err.message);
      }
    }
  }

  _safeSend(obj) {
    if (this.ws.readyState === this.ws.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  async finish() {
    if (this.closed) return this._cachedRecord || this.toJSON();
    this.closed = true;
    if (this.restartTimer) clearTimeout(this.restartTimer);
    if (this.geminiStream) {
      try {
        this.geminiStream.end();
      } catch (_) {
        /* noop */
      }
    }
    await this._stopRecording();
    this.endedAt = new Date().toISOString();
    const record = this.toJSON();
    this._cachedRecord = record;
    this._persist(record);
    return record;
  }

  // ffmpegへの入力を締めて、MP4への書き出しが完了するまで待つ
  _stopRecording() {
    return new Promise((resolve) => {
      const proc = this.ffmpegProcess;
      if (!proc) return resolve();

      proc.once('close', (code) => {
        if (code !== 0) {
          console.error(`[session ${this.id}] ffmpeg exited with code ${code}`);
          this.recordingFailed = true;
        } else {
          console.log(`[session ${this.id}] recording saved -> ${path.join(RECORDINGS_DIR, this.audioFileName)}`);
        }
        resolve();
      });
      proc.once('error', () => {
        this.recordingFailed = true;
        resolve();
      });
      try {
        proc.stdin.end();
      } catch (_) {
        this.recordingFailed = true;
        resolve();
      }
    });
  }

  toJSON() {
    const hasAudio = this.audioFileName && !this.recordingFailed;
    return {
      sessionId: this.id,
      languageCode: this.languageCode,
      model: MODEL,
      startedAt: this.startedAt,
      endedAt: this.endedAt,
      segments: this.segments,
      fullText: this.segments.map((s) => s.text).join(''),
      audioFile: hasAudio ? this.audioFileName : null,
      audioUrl: hasAudio ? `/recordings/${this.audioFileName}` : null,
    };
  }

  _persist(record) {
    const filename = `${this.startedAt.replace(/[:.]/g, '-')}_${this.id}.json`;
    const filePath = path.join(TRANSCRIPTS_DIR, filename);
    fs.writeFile(filePath, JSON.stringify(record, null, 2), (err) => {
      if (err) {
        console.error(`[session ${this.id}] failed to persist transcript:`, err.message);
      } else {
        console.log(`[session ${this.id}] transcript saved -> ${filePath}`);
      }
    });
  }
}

wss.on('connection', (ws) => {
  let session = null;

  ws.on('message', (data, isBinary) => {
    if (isBinary) {
      if (session) session.writeAudio(data);
      return;
    }

    let msg;
    try {
      msg = JSON.parse(data.toString());
    } catch (_) {
      return;
    }

    if (msg.type === 'start') {
      if (session) session.finish().catch(() => {});
      session = new TranscriptionSession(ws, {
        sampleRateHertz: msg.sampleRate || 48000,
        languageCode: msg.languageCode,
      });
      session.start();
      ws.send(JSON.stringify({ type: 'started', sessionId: session.id }));
    } else if (msg.type === 'stop') {
      if (session) {
        const current = session;
        session = null;
        current.finish().then((record) => {
          current._safeSend({ type: 'stopped', session: record });
        });
      }
    }
  });

  ws.on('close', () => {
    if (session) session.finish().catch(() => {});
    session = null;
  });
});

server.listen(PORT, () => {
  console.log(`voicehack server listening on http://localhost:${PORT}`);
});
