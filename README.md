# voicehack

入力した音声について,

| 機能 | 出力 | 実装の根拠 (論文・規格) |
|---|---|---|
| スペクトル表示 | `spectrum.png` (原音 / 音声 / 環境音を重ね描き, 主要ピーク注記) | Welch 1967 (平均ピリオドグラム), Harris 1978 (Hann 窓) |
| スペクトログラム表示 | `spectrogram.png` (線形周波数 + メル 80 帯域, F0 重ね描き) | Allen 1977 (STFT/OLA), O'Shaughnessy 1987 (mel 尺度) |
| 周波数・大きさの数値化 | `report.json` / `frames.csv` (フレーム毎) | 主要ピーク (放物線補間), オクターブバンド (IEC 61260), スペクトル重心 / 広がり / rolloff / 平坦度 (Peeters 2004, Johnston 1988) |
| ノイズ分離 (環境音だけ取り出す) | `speech.wav`, `environment.wav`, `separation.png` | MCRA 雑音推定 (Cohen & Berdugo 2002) + MMSE-LSA (Ephraim & Malah 1985) + decision-directed 事前 SNR (Ephraim & Malah 1984) + OM-LSA の存在確率ゲート (Cohen & Berdugo 2001) + 周期性による音声区間ゲート (Tucker 1992) |
| 大きさ | LUFS (integrated / momentary / short-term), LRA, RMS dBFS, peak, crest factor | ITU-R BS.1770-4, EBU Tech 3341 / 3342 |
| トーン (声の高さ・抑揚) | F0 輪郭, 中央値 (Hz と音名), 5–95% 範囲, 抑揚幅 (半音) | YIN (de Cheveigné & Kawahara 2002) + 話者音域の 2 パス推定 (Hirst 2011) |
| スピード | 音節数, 発話速度 / 調音速度 [音節/s], ポーズ数 | 音節核検出 (de Jong & Wempe 2009) |

大きさ・トーン・スピードは `prosody.png` にまとめて描画されます. トーンとスピードは, 分離した **音声側** の信号で測ります.

## セットアップ

```bash
cd ~/voicehack
uv sync                 # マイク録音も使うなら: uv sync --extra mic
```

## 使い方

```bash
# ファイルを解析 (wav / flac / ogg / mp3. m4a は ffmpeg 等で wav に変換してから)
uv run voicehack analyze path/to/voice.wav            # -> out/voice/
uv run voicehack analyze voice.wav -o out/test --sr 16000
uv run voicehack analyze voice.wav --no-separate      # ノイズ分離なし

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

## 検証 (`uv run pytest`)

- K 特性フィルタ係数が BS.1770-4 の 48 kHz 係数表と 1e-8 以内で一致
- 997 Hz / 0 dBFS 正弦波 → −3.01 LUFS (規格の校正条件; 16 / 44.1 / 48 kHz で確認)
- YIN: 90–380 Hz の調波音で誤差 0.1 半音未満, 白色雑音はほぼ無声判定
- STFT → iSTFT の完全再構成, 分離の加法性 (speech + environment = 入力)
- 合成音声 + 白色雑音で分離後 SNR が 5 dB 超改善
- 先頭にデジタル無音 (マイク起動直後) があっても雑音推定が破綻しない

実マイク録音 (MacBook Air 内蔵マイク) では, YIN の有声判定を Praat と照合して
既定値を決めました (再現率 0.73 / 適合率 0.92 / 1 半音超の誤り 5.7%; F0 中央値は Praat・pYIN と一致).

デモ音声 (`say` の日本語音声 + ピンクノイズ・ハム・電子音, 入力 SNR ≈ 1 dB) では,
分離後の音声 SNR が約 +6.5 dB 改善 (1.0 → 7.6 dB). 読み上げ速度を変えた 3 種で,
測定した発話速度の比 (fast / slow = 1.67) は実際の発話時間の比 (1.66) と一致しました.

## 既知の限界

- **突発音の分離は「声と重なっていない」場合のみ.** MCRA は定常雑音 (空調・ハムなど) を,
  周期性ゲートは声から離れた突発音 (机を叩く音・拍手・クリック) を環境音側に回します.
  実録音で, 声から離れた打撃音 10 回すべてで声側に残る割合が 29〜80% → 0.3% に, 声の保持率は 99.8〜100%.
  一方, **発話と同時に鳴った突発音は分けられません** (机を叩きながら話した例で 39〜99% が声側に残る).
  これには学習ベースの手法 (例: Conv-TasNet, Luo & Mesgarani 2019 / DeepFilterNet, Schröter et al. 2022) が必要です.
- 周期的な環境音 (楽器・電子音・他人の声) は「声」と判定されます.
- 雑音の初期推定は「録音中で最も静かな 20% のフレーム」から取ります. 雑音レベルが大きく変わる場合は, MCRA が 1〜2 秒かけて追従します.
- 音節核検出は音節数を少なめに数える傾向があります (原論文も絶対数ではなく人手計数との相関で評価).
  速度の **比較** には向いていますが, 絶対値の目安にとどめてください. 日本語のモーラ数とは一致しません.
- 発話速度は録音全長ではなく「最初〜最後の音節核」の区間で割っています
  (原論文は全長. `speech_rate_total_syll_per_s` に原論文の定義の値も出力).

## 参考文献

- P. D. Welch, IEEE Trans. Audio Electroacoust. 15(2), 1967.
- F. J. Harris, Proc. IEEE 66(1), 1978.
- J. B. Allen, IEEE TASSP 25(3), 1977.
- G. Peeters, CUIDADO project report, IRCAM, 2004.
- J. D. Johnston, IEEE JSAC 6(2), 1988.
- ITU-R BS.1770-4 (2015); EBU Tech 3341 / 3342 (2016).
- A. de Cheveigné, H. Kawahara, JASA 111(4), 2002.
- D. Hirst, Journal of Speech Sciences 1(1), 2011.
- I. Cohen, B. Berdugo, IEEE SPL 9(1), 2002 (MCRA); Signal Processing 81(11), 2001 (OM-LSA).
- Y. Ephraim, D. Malah, IEEE TASSP 32(6), 1984; 33(2), 1985.
- O. Cappé, IEEE TSAP 2(2), 1994.
- R. Tucker, IEE Proceedings-I 139(4), 1992.
- N. H. de Jong, T. Wempe, Behavior Research Methods 41(2), 2009.
