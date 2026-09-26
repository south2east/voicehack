# voicehack

Google Cloud Speech-to-Text (Chirp 3) を使ったリアルタイム文字起こしアプリ。
「開始」ボタンを押すとマイク入力がストリーミングでテキスト化され、「終了」ボタンで
セッションを締めて結果を JSON として保存・ダウンロードできます。

## 構成

- `server.js` — Express + WebSocket サーバー。ブラウザから受け取った音声(PCM16)を
  Google Cloud Speech-to-Text v2 API の `streamingRecognize` に中継し、認識結果を
  ブラウザへリアルタイムに返します。セッション終了時に `transcripts/` へ JSON を保存します。
- `public/` — フロントエンド (素のHTML/CSS/JS)。マイクを `AudioContext` で取得し、
  16bit PCM に変換して WebSocket で送信します。
- `transcripts/` — セッションごとの文字起こし結果 (JSON) の保存先 (git管理外)。

## セットアップ

```bash
npm install
cp .env.example .env
```

`.env` を編集し、以下を設定してください。

| 変数 | 説明 |
| --- | --- |
| `GOOGLE_APPLICATION_CREDENTIALS` | サービスアカウントキー(JSON)へのパス |
| `GOOGLE_CLOUD_PROJECT` | GCPプロジェクトID |
| `GOOGLE_CLOUD_LOCATION` | Speech-to-Text の実行リージョン。Chirp 3 のストリーミングは `us` / `eu` マルチリージョンのみ GA (既定 `us`) |
| `SPEECH_MODEL` | 認識モデル。既定値は `chirp_3`。使えない場合は `chirp_2` / `chirp` / `latest_long` にフォールバック |
| `SPEECH_LANGUAGE` | 認識言語 (既定 `ja-JP`) |
| `PORT` | サーバーのポート (既定 `3000`) |

Google Cloud 側の事前準備:

1. GCPプロジェクトで **Cloud Speech-to-Text API** を有効化
2. サービスアカウントを作成し、`roles/speech.client` (または同等の権限) を付与してキー(JSON)を発行
3. 発行した JSON を `GOOGLE_APPLICATION_CREDENTIALS` が指すパスに配置

## 起動

```bash
npm start
# または開発時: npm run dev
```

ブラウザで `http://localhost:3000` を開き、「開始」ボタンでマイクの利用を許可すると
録音・リアルタイム文字起こしが始まります。「終了」ボタンでセッションを終了すると:

- 確定した文字起こし結果が `transcript_<sessionId>.json` としてダウンロード可能になる
- サーバー側にも `transcripts/<timestamp>_<sessionId>.json` として保存される

### JSON フォーマット

```json
{
  "sessionId": "uuid",
  "languageCode": "ja-JP",
  "model": "chirp_3",
  "startedAt": "2026-01-01T00:00:00.000Z",
  "endedAt": "2026-01-01T00:01:23.000Z",
  "segments": [
    { "text": "こんにちは", "confidence": 0.98, "receivedAt": "2026-01-01T00:00:05.000Z" }
  ],
  "fullText": "こんにちは..."
}
```

## 既知の制約

- ブラウザの `ScriptProcessorNode` を使用 (非推奨API) — 動作はするが、将来的には
  `AudioWorklet` への置き換えが望ましい
- Google の `streamingRecognize` は1ストリームあたり最大 ~5分の制約があるため、
  サーバー側で約4分ごとにストリームを自動的に張り直して継続する実装になっている
  (再接続の瞬間にごく短い認識の途切れが発生し得る)
- HTTPS/WSS 配信でない場合、ブラウザによっては `localhost` 以外でのマイクアクセスが
  ブロックされる点に注意
