# voicehack

音声を入力として, リアルタイム文字起こしと, 声の特徴 (大きさ・高さ・速さ) の解析・雑音分離を行うツール群です.

| 構成 | 言語 | 場所 | 概要 |
|---|---|---|---|
| [1. リアルタイム文字起こしアプリ](#1-リアルタイム文字起こしアプリ-nodejs) | Node.js | `server.js`, `public/` | ブラウザのマイク入力を Google Cloud Speech-to-Text (Chirp 3) でストリーミング文字起こし. 録音を保存し, 終了後に 2. の解析ツールへ自動で渡す |
| [2. 音声解析ツール](#2-音声解析ツール-python) | Python | `src/voicehack/`, `experiments/`, `tests/` | スペクトル・大きさ (LUFS)・声の高さ・話す速さの計測, 声と環境音の分離 |
| 調査ノート | — | `research_notes/`, `reports/` | 録音データのノイズ除去手法の調査 |

**1 と 2 は連携しています.** Web アプリで「終了」を押すと, 録音を Python の解析ツールに渡し,
結果 (大きさ・声の高さ・話す速さ, 元の音 / 声だけ / 環境音だけの聞き比べ, 図) を同じ画面に表示します.

```
ブラウザ ─ マイク ─▶ server.js ─┬─▶ Google Speech-to-Text (生の音のまま. リアルタイム文字起こし)
                                └─▶ recordings/: MP4 と WAV で保存
「終了」 → uv run voicehack analyze recordings/<name>.wav --sr 16000 --transcript <文字起こし> [--dnn]
           (バックグラウンドで 1 件ずつ実行. 文字起こしからモーラ/秒を計算し, Whisper は使わない)
         → out/sessions/<name>/ (report.json, 図, speech.wav, environment.wav)
         → 画面が GET /api/sessions/<id> で完了を確認し「音声解析」欄に表示
```

- 文字起こし (STT) には **ノイズ除去していない生の音** を渡します. 分離後の音を認識させると誤りが増えるため
  (本リポジトリの実測, および `reports/録音データのノイズ除去手法.md` の調査結果).
- 「声と重なった物音も分離する」にチェックすると `--dnn` で解析します (15 秒の録音で約 15 秒).
  外すと数秒で終わります.
- 「音声解析」欄には数値 (大きさ・トーン・スピード・環境音), 元の音 / 声だけ / 環境音だけの聞き比べ, 図が出ます.
- 使うには 1 と 2 の両方のセットアップが必要です (`npm install` と `uv sync --extra asr`).
  解析の有効/無効や引数は `.env` の `ANALYSIS_ENABLED` / `ANALYSIS_CMD` / `ANALYSIS_ARGS` で変えられます.

どちらも設定はリポジトリ直下の **`.env`** (ひな形: `.env.example`) から読みます. `.env` は git 管理外です.

```bash
cp .env.example .env   # 使う機能の項目だけ埋めればよい
```

---

## 1. リアルタイム文字起こしアプリ (Node.js)

Google Cloud Speech-to-Text (Chirp 3) を使ったリアルタイム文字起こしアプリ。
「開始」ボタンを押すとマイク入力がストリーミングでテキスト化され、「終了」ボタンで
セッションを締めて結果を JSON として保存・ダウンロードできます。
同じ音声は録音として保存され、終了後に [2. 音声解析ツール](#2-音声解析ツール-python) で
大きさ・トーン・スピードなどを自動で解析し、画面に表示します。

### 構成

- `server.js` — Express + WebSocket サーバー。ブラウザから受け取った音声(PCM16)を
  Google Cloud Speech-to-Text v2 API の `streamingRecognize` に中継し、認識結果を
  ブラウザへリアルタイムに返します。同じ音声を ffmpeg-static (npm 同梱) で録音ファイルに
  書き出し、セッション終了後に `uv run voicehack analyze` をバックグラウンドで実行します。
  セッション終了時と解析完了時に `transcripts/` へ JSON を保存します。
- `public/` — フロントエンド (素のHTML/CSS/JS)。マイクを `AudioContext` で取得し、
  16bit PCM に変換して WebSocket で送信します。
- `transcripts/` — セッションごとの文字起こし結果 (JSON) の保存先 (git管理外)。
- `recordings/` — 録音の保存先 (git管理外)。ダウンロード用の MP4 (AAC) と解析用の WAV (16bit PCM) を保存。
- `out/sessions/<timestamp>_<sessionId>/` — 解析結果 (`report.json`, 図, 分離音声) の保存先 (git管理外)。

### セットアップ

```bash
npm install
uv sync          # 録音の解析を使う場合 (2. のセットアップ参照)
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
| `ANALYSIS_ENABLED` | `false` にすると終了後の音声解析をしない (既定: 解析する) |
| `ANALYSIS_CMD` | 解析ツールの起動コマンド (既定 `uv run voicehack`) |
| `ANALYSIS_ARGS` | `voicehack analyze` に渡す追加オプション (既定 `--sr 16000`。例: `--sr 16000 --asr groq`) |

Google Cloud 側の事前準備:

1. GCPプロジェクトで **Cloud Speech-to-Text API** を有効化
2. サービスアカウントを作成し、`roles/speech.client` (または同等の権限) を付与してキー(JSON)を発行
3. 発行した JSON を `GOOGLE_APPLICATION_CREDENTIALS` が指すパスに配置

### 起動

```bash
npm start
# または開発時: npm run dev
```

ブラウザで `http://localhost:3000` を開き、「開始」ボタンでマイクの利用を許可すると
録音・リアルタイム文字起こしが始まります。「終了」ボタンでセッションを終了すると:

- 確定した文字起こし結果が `transcript_<sessionId>.json` として、録音が MP4 としてダウンロード可能になる
- サーバー側にも `transcripts/<timestamp>_<sessionId>.json` として保存される
- 録音の解析がバックグラウンドで始まり、完了すると「音声解析」欄に大きさ・トーン・スピード・環境音の
  数値と図が表示される (録音の長さと同程度の時間がかかる。解析は 1 件ずつ順番に実行)

`uv` や Python 環境が無い場合も文字起こしと録音は動き、解析欄にエラーが表示されるだけです。

#### JSON フォーマット

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
  "fullText": "こんにちは...",
  "audioFile": "<timestamp>_<sessionId>.mp4",
  "audioUrl": "/recordings/<timestamp>_<sessionId>.mp4",
  "wavFile": "<timestamp>_<sessionId>.wav",
  "analysis": {
    "status": "done",
    "reportUrl": "/analysis/<timestamp>_<sessionId>/report.json",
    "images": ["/analysis/<timestamp>_<sessionId>/prosody.png", "..."],
    "report": { "level": {}, "loudness": {}, "pitch": {}, "rate": {}, "spectrum": {}, "separation": {} }
  }
}
```

`analysis.status` は `running` → `done` / `failed` / `skipped` (録音なし) / `disabled` と変化します。
解析中の状態は `GET /api/sessions/<sessionId>` で取得できます (サーバー起動中に終了したセッションのみ)。

### 既知の制約

- ブラウザの `ScriptProcessorNode` を使用 (非推奨API) — 動作はするが、将来的には
  `AudioWorklet` への置き換えが望ましい
- Google の `streamingRecognize` は1ストリームあたり最大 ~5分の制約があるため、
  サーバー側で約4分ごとにストリームを自動的に張り直して継続する実装になっている
  (再接続の瞬間にごく短い認識の途切れが発生し得る)
- HTTPS/WSS 配信でない場合、ブラウザによっては `localhost` 以外でのマイクアクセスが
  ブロックされる点に注意

---

## 2. 音声解析ツール (Python)

話し声を録音・解析して, **スペクトル / スペクトログラム, 大きさ (LUFS), 声の高さと抑揚, 話す速さ** を数値と図で出し,
**声と環境音を分離** する Python ツールです. 各処理は論文・規格にもとづいて実装し, 実録音で評価しています.

入力した音声について,

| 機能 | 出力 | 実装の根拠 (論文・規格) |
|---|---|---|
| スペクトル表示 | `spectrum.png` (原音 / 音声 / 環境音を重ね描き, 主要ピーク注記) | Welch 1967 (平均ピリオドグラム), Harris 1978 (Hann 窓) |
| スペクトログラム表示 | `spectrogram.png` (線形周波数 + メル 80 帯域, F0 重ね描き) | Allen 1977 (STFT/OLA), O'Shaughnessy 1987 (mel 尺度) |
| 周波数・大きさの数値化 | `report.json` / `frames.csv` (フレーム毎) | 主要ピーク (放物線補間), オクターブバンド (IEC 61260), スペクトル重心 / 広がり / rolloff / 平坦度 (Peeters 2004, Johnston 1988) |
| ノイズ分離 (環境音だけ取り出す) | `speech.wav`, `environment.wav`, `separation.png`; `--dnn` で声と重なった物音も分離 | MCRA 雑音推定 (Cohen & Berdugo 2002) + MMSE-LSA (Ephraim & Malah 1985) + decision-directed 事前 SNR (Ephraim & Malah 1984) + OM-LSA の存在確率ゲート (Cohen & Berdugo 2001) + 周期性による音声区間ゲート (Tucker 1992); `--dnn`: SepFormer (Subakan et al. 2021, DNS4 学習済み) を発話単位で適用 |
| 大きさ | LUFS (integrated / momentary / short-term), LRA, RMS dBFS, peak, crest factor | ITU-R BS.1770-4, EBU Tech 3341 / 3342 |
| トーン (声の高さ・抑揚) | F0 輪郭, 中央値 (Hz と音名), 5–95% 範囲, 抑揚幅 (半音) | YIN (de Cheveigné & Kawahara 2002) + 話者の声域 (Hirst 2011) と輪郭の連続性による外れ値除去 |
| スピード | 音節数, 発話速度 / 調音速度 [音節/s], ポーズ数, 変調周波数 [Hz]; `--asr` でモーラ速度 [モーラ/s] と文字起こし | サブバンド相関による音節核検出 (Wang & Narayanan 2007, 主指標), 強度ピーク法 (de Jong & Wempe 2009, 参考), 包絡変調スペクトル (Morgan & Fosler-Lussier 1998, mrate); `--asr`: Whisper (Radford et al. 2023) + UniDic の読みでモーラを数える |

大きさ・トーン・スピードは `prosody.png` にまとめて描画されます. トーンとスピードは, 分離した **音声側** の信号で測ります.

### セットアップ

動作確認環境: macOS (Apple Silicon), Python 3.11. Linux でも動きますが, マイク録音の開始音・`afplay`・
キーチェーン・デモ音声作成 (`say`) は macOS 前提です.

```bash
# 1. uv (Python のパッケージ管理ツール) が無ければ入れる
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 取得して依存をインストール
git clone https://github.com/south2east/voicehack.git
cd voicehack
git switch yuta-qwerty                       # main にマージされるまではこのブランチ
uv sync --extra mic                          # 基本機能 + マイク録音
uv sync --extra mic --extra asr --extra dnn  # 全部入り (音声認識 + 深層学習による分離, 約 1.2 GB)

# 3. Groq で文字起こしする場合だけ: 自分の API キーを .env に書く
cp .env.example .env                         # .env の GROQ_API_KEY= に自分のキーを書く (.env は git に入らない)

# 4. 動作確認
uv run pytest -q
uv run voicehack record -d 10 --asr          # ピッと鳴ったら話す
```

| extra | 入るもの | 使う機能 |
|---|---|---|
| (なし) | numpy, scipy, matplotlib, soundfile | 解析・従来法の分離 |
| `mic` | sounddevice | `record` (マイク録音) |
| `asr` | faster-whisper, fugashi, unidic-lite | `--asr` (ローカル文字起こし). 初回に Whisper small (約 480 MB) を取得 |
| `dnn` | torch, torchaudio, speechbrain | `--dnn` (声と重なった物音の分離). 初回に SepFormer (約 110 MB) を取得 |

`--asr groq` は追加パッケージ不要です (API キーのみ).

### 使い方

```bash
# ファイルを解析 (wav / flac / ogg / mp3. m4a は ffmpeg 等で wav に変換してから)
uv run voicehack analyze path/to/voice.wav            # -> out/voice/
uv run voicehack analyze voice.wav -o out/test --sr 16000
uv run voicehack analyze voice.wav --no-separate      # ノイズ分離なし
uv run voicehack analyze voice.wav --asr              # 音声認識でモーラ速度も (要: uv sync --extra asr,
                                                      #  初回に Whisper small 約 480 MB をダウンロード)
uv run voicehack analyze voice.wav --asr groq         # 文字起こしを Groq Cloud で (高速・高精度, 音声を外部送信)
uv run voicehack analyze voice.wav --dnn              # 声と重なった物音も分離 (要: uv sync --extra dnn,
                                                      #  torch 等 約 600 MB + 初回にモデル約 110 MB)

# マイクで 5 秒録音して解析
uv run voicehack record -d 5                          # -> out/recording/

# デモ音声を作る (macOS の `say` 音声 + 合成環境音)
uv run python samples/make_samples.py
uv run voicehack analyze samples/normal_noisy.wav
```

出力ディレクトリの中身:

```
report.json      数値のまとめ (level / loudness / spectrum / pitch / rate / separation)
frames.csv       8 ms 毎の RMS, momentary LUFS, F0, 重心, rolloff, 平坦度, 音声存在確率
spectrum.png     長時間平均スペクトル
spectrogram.png  スペクトログラム + メルスペクトログラム + F0
prosody.png      波形 / 大きさ / トーン / スピード
separation.png   原音・音声・環境音のスペクトログラムと分離マスク
speech.wav       分離した音声
environment.wav  分離した環境音 (= 原音 − 音声. 足すと元に戻る)
```

### Groq API キーの設定 (`--asr groq` を使う場合のみ)

`--asr groq` は発話ごとの音声を Groq Cloud (`whisper-large-v3-turbo`) に送って文字起こしします.
**録音した音声が Mac の外に送信されます.** `--asr` (ローカル) では送信されません.

API キーは **macOS のキーチェーンに保存** し, voicehack が使う瞬間にだけ読み出します.
リポジトリにも `~/.zshrc` にも書かないので, どちらを GitHub に公開してもキーは漏れません.

```bash
# キーチェーンに保存 (キーは対話入力. コマンド履歴に残らない)
security add-generic-password -a "$USER" -s GROQ_API_KEY -w

# 保存できたか確認 (キーの中身は表示しない)
security find-generic-password -a "$USER" -s GROQ_API_KEY >/dev/null && echo "saved"

# 削除 (キーを変えるときは削除してから保存し直す)
security delete-generic-password -a "$USER" -s GROQ_API_KEY
```

`.env` ファイルでも設定できます (ひな形は `.env.example`). `.env` は `.gitignore` で除外済みですが,
平文で置かれる点に注意してください.

```bash
cp .env.example .env    # その後 .env の GROQ_API_KEY= にキーを書く
```

キーを探す順番は 環境変数 `GROQ_API_KEY` → `.env` → キーチェーン です.
`.gitignore` は `.env` / `.env.*` (`.env.example` を除く) / `*.key` を除外しています.

### 評価用データについて

`experiments/eval_rate.py` と `experiments/eval_overlap.py` は, 作者の実録音 (`out/` 以下) を使います.
録音は個人の声なのでリポジトリには含めていません. 自分で評価する場合は, 各スクリプト冒頭の説明に沿って
同じ条件で録音し (`uv run voicehack record ...`), 区間の定義を書き換えてください.

### 検証 (`uv run pytest`)

- K 特性フィルタ係数が BS.1770-4 の 48 kHz 係数表と 1e-8 以内で一致
- 997 Hz / 0 dBFS 正弦波 → −3.01 LUFS (規格の校正条件; 16 / 44.1 / 48 kHz で確認)
- YIN: 90–380 Hz の調波音で誤差 0.1 半音未満, 白色雑音はほぼ無声判定
- STFT → iSTFT の完全再構成, 分離の加法性 (speech + environment = 入力)
- 合成音声 + 白色雑音で分離後 SNR が 5 dB 超改善
- 先頭にデジタル無音 (マイク起動直後) があっても雑音推定が破綻しない

実マイク録音 (MacBook Air 内蔵マイク) では, YIN の有声判定を Praat と照合して
既定値を決めました (再現率 0.73 / 適合率 0.92 / 1 半音超の誤り 5.7%; F0 中央値は Praat・pYIN と一致).
声域の外まで滑らかに上がる声も追えるよう, 声域で探索範囲を切らずに「跳び離れた断片だけ捨てる」方式にしています
(実録音 9 本で Praat 比 20% 超の誤り 4.9% → 4.3%, 「あー」上昇の 250 Hz 超の追跡 15/27 → 25/27 フレーム).

デモ音声 (`say` の日本語音声 + ピンクノイズ・ハム・電子音, 入力 SNR ≈ 1 dB) では,
分離後の音声 SNR が約 +6.5 dB 改善 (1.0 → 7.6 dB). 読み上げ速度を変えた 3 種で,
測定した発話速度の比 (fast / slow = 1.67) は実際の発話時間の比 (1.66) と一致しました.

### 既知の限界

- **突発音の分離.** 従来法 (既定) は, 声から離れた突発音 (机・拍手・クリック) を周期性ゲートで環境音側に回します
  (実録音 10 回で声側の残り 29〜80% → 0.3%, 語末「す」の保持 82〜100%). 声と同時に鳴った突発音は分けられません.
  **`--dnn`** では学習済みの SepFormer を発話ごとに掛けるので, 声と重なった突発音も分けられます
  (実録音で 37.5% / 98.5% → 6.3% / 7.2%; 合成評価で SI-SDR 13.7 → 18.6 dB). CPU で音声の約 0.5 倍の時間がかかります.
  SepFormer を長い区間にまとめて掛けると, 大きな突発音を含む録音で一部の発話が消える失敗があったため,
  発話単位で処理し, 有声フレームの低域で従来法と大きく食い違う発話は従来法の出力に戻す安全網を入れています.
- **背景の音楽・TV.** 実録音 (スマホで流しながら話す, 話し声より 10〜15 dB 小さい音量) で,
  音楽 (歌あり・歌なし) は声と判定されず, 音楽だけの区間で声側に入った音楽は 従来法 -25 dB / `--dnn` -53〜-57 dB.
  TV の人の声は周期性があり DNN も声として残すため, そのままでは声側に入ります (-19 dB).
  そこで **最大の発話より 15 dB 以上小さい声の区間は遠くの声 (環境音) とみなします** (本人の発話は 0〜-10 dB,
  TV は -23 dB だった). これで TV の声は 従来法 -25 dB / `--dnn` -55 dB に. 小さな声も残したい場合は `--keep-far-voices`.
  背景の声が本人と同程度の大きさ (隣の人の会話など) だと分けられません (話者を登録して抽出する手法が必要).
- **ささやき声は環境音側に分類されます** (周期性が無いため). 実録音で, 従来法は声側 -25 dB (認識「今日は良い電気」),
  `--dnn` は発話区間から外れて声側に残りません. SepFormer はささやき声だけを切り出すと雑音とみなして消し,
  普通の声を含む長い文脈ごとに処理すると残す (+1.5 dB) ものの, その処理では机の音も声として残るため採用していません.
- 雑音の初期推定は「録音中で最も静かな 20% のフレーム」から取ります. 雑音レベルが大きく変わる場合は, MCRA が 1〜2 秒かけて追従します.
- **スピードは `--asr` のモーラ速度が最も正確.** 同じ文 × 3 速度の実録音 (`experiments/eval_rate.py`) で,
  速さの比の誤差は test 3.5% / dev 4.5% (音量包絡の方式は test 24%), モーラ/s の絶対値の誤差は 8% 前後.
  読みの揺れ (「明日」をアス, 「私」をワタクシと読むなど) で ±1 モーラずれることがあります.
  認識は Mac 内で完結し, 音声を外部に送りません.
- **音量包絡によるスピードは比較用の指標.** 同じ文を速さを変えて読んだ音声 5 本で, 推定した速さの比の誤差は
  Wang & Narayanan 法で 6 / 8 / 20% (de Jong 法は 8 / 24 / 25%). ただしパラメータもこの 5 本で選んでいます.
  パラメータ選択に使っていない実録音 (同じ文を 普通 / ゆっくり / 早口) では, 比の誤差は
  普通/ゆっくり 20% (de Jong 33%), 早口/ゆっくり 26% (de Jong 36%), 早口/普通 8% (de Jong 4%).
  どの方式も速さの差を実際より小さく見積もる (比が圧縮される) 傾向があります.
  `experiments/eval_rate.py` (同じ文 × 3 速度の実録音, dev / test 分割) では test の比の誤差 24%.
  早口 (10〜14 モーラ/s) で推定値が 5〜6 音節/s に頭打ちになるのが主因で,
  パラメータ調整は dev でしか効きませんでした (dev 18.5% → 8.5%, test 24.0% → 25.4%).
  絶対値は音節を少なめに数えるため実際より低く出ます (日本語のモーラ数とも一致しません).
  伸ばした母音 (「あー」) は抑揚の揺れで複数の音節に数えられることがあります.
  変調周波数 (mrate) は音節を数えない補助指標ですが, 速さの違いに対する感度は低めです.
- 発話速度は録音全長ではなく「最初〜最後の音節核」の区間で割っています.

### 参考文献

- P. D. Welch, IEEE Trans. Audio Electroacoust. 15(2), 1967.
- F. J. Harris, Proc. IEEE 66(1), 1978.
- J. B. Allen, IEEE TASSP 25(3), 1977.
- G. Peeters, CUIDADO project report, IRCAM, 2004.
- J. D. Johnston, IEEE JSAC 6(2), 1988.
- ITU-R BS.1770-4 (2015); EBU Tech 3341 / 3342 (2016).
- A. de Cheveigné, H. Kawahara, JASA 111(4), 2002.
- D. Hirst, Journal of Speech Sciences 1(1), 2011.
- P. Boersma, Proc. Institute of Phonetic Sciences Amsterdam 17, 1993.
- I. Cohen, B. Berdugo, IEEE SPL 9(1), 2002 (MCRA); Signal Processing 81(11), 2001 (OM-LSA).
- Y. Ephraim, D. Malah, IEEE TASSP 32(6), 1984; 33(2), 1985.
- O. Cappé, IEEE TSAP 2(2), 1994.
- R. Tucker, IEE Proceedings-I 139(4), 1992.
- C. Subakan et al., "Attention is all you need in speech separation," ICASSP 2021 (SepFormer).
- J. Le Roux et al., "SDR – half-baked or well done?," ICASSP 2019 (SI-SDR).
- N. H. de Jong, T. Wempe, Behavior Research Methods 41(2), 2009.
- D. Wang, S. S. Narayanan, IEEE TASLP 15(8), 2007.
- N. Morgan, E. Fosler-Lussier, Proc. ICASSP, 1998.
- A. Radford et al., "Robust speech recognition via large-scale weak supervision," ICML 2023 (Whisper).

### 使用している外部モデル・データ

| 名前 | 用途 | ライセンス |
|---|---|---|
| Whisper small (`Systran/faster-whisper-small`) | `--asr` のローカル文字起こし | MIT |
| Groq `whisper-large-v3-turbo` | `--asr groq` (クラウド API, 各自の API キーで利用) | Groq の利用規約に従う |
| SepFormer (`speechbrain/sepformer-dns4-16k-enhancement`) | `--dnn` の音声強調 | Apache-2.0 |
| UniDic (unidic-lite) | 読み仮名 → モーラ数 | BSD-3-Clause |
