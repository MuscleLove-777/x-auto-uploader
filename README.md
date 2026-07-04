# X Auto Uploader

Google Driveの動画・画像をX (Twitter)に自動投稿するシステム。
GitHub Actionsで自動実行（現行スケジュールはJST 12:00の1日1回、`.github/workflows/upload.yml`参照）。

- 対象メディア: mp4 / mov / jpg / jpeg / png / webp / gif からランダム選択
- 文面: content_pool（毎日自動更新）＋ローカルの俺口調テンプレを合わせた候補からランダム生成
- ハッシュタグ: ブランド核タグ＋Google Trends（取得失敗時は曜日・季節・コミュニティタグへ自動フォールバック）
- 文字数: X基準の重み付きカウント（日本語=2字、URL=23字換算）で280字以内を保証

## セットアップ手順

### 1. X Developer Portalでアプリ作成

1. https://developer.x.com/en/portal/dashboard にアクセス
2. 「Free」プランでサインアップ（無料）
3. 新しいProjectとAppを作成
4. App Settings → 「User authentication settings」→ Edit
   - App permissions: **Read and Write**
   - Type of App: **Web App**
   - Callback URL: `https://example.com/callback`（使わないがダミーで必要）
   - Website URL: 任意
5. 「Keys and Tokens」タブで以下を取得:
   - API Key (= Consumer Key)
   - API Key Secret (= Consumer Secret)
   - Access Token
   - Access Token Secret

### 2. 認証（ローカルで実行）

```bash
pip install requests requests-oauthlib
python x_auth.py
```

画面の指示に従ってブラウザで認証 → PINコードを入力。

### 3. GitHubリポジトリ作成 & Secrets設定

```bash
gh repo create x-auto-uploader --private --source=. --push
```

GitHub → Settings → Secrets and variables → Actions で以下を追加:

| Secret名 | 値 |
|-----------|-----|
| `X_CONSUMER_KEY` | API Key |
| `X_CONSUMER_SECRET` | API Key Secret |
| `X_ACCESS_TOKEN` | Access Token |
| `X_ACCESS_TOKEN_SECRET` | Access Token Secret |
| `GDRIVE_FOLDER_ID` | Google DriveフォルダID |

### 4. 動作確認

Actions → X Auto Upload → Run workflow で手動実行してテスト。

## フォルダ構成（Google Drive）

既存のTumblr/DeviantArtと同じGoogle Driveフォルダを使用可能。

```
Google Drive フォルダ/
├── training/
│   ├── video1.mp4
│   └── video2.mp4
├── posing/
│   └── video3.mp4
├── nsfw/          ← 自動的にpossibly_sensitive=trueに
│   └── video4.mp4
└── ...
```

- フォルダ名/ファイル名からハッシュタグを自動生成
- `nsfw`, `sexy`, `adult`, `bikini`等のキーワードが含まれると自動でセンシティブ設定

## 制限事項（Free Tier）

- 月500ツイートまで（1日4回 × 30日 = 120回なので余裕）
- 動画は最大140秒・512MB（MP4/MOV）
- 画像は最大5MB（JPG/PNG/WEBP）、GIFは最大15MB
- 読み取りAPI（タイムライン取得等）はほぼ使用不可

## Additional options (2026-05)

- `X_SCHEDULE_GUARD` (`true`/`false`, default: `true`)
  - Restrict live posting to configured JST windows.
- `X_POST_HOURS_JST` (default: `0,6,12,18`)
  - Comma-separated allowed JST hours. `24` is treated as `0`.
- `X_POST_WINDOW_MINUTES` (default: `20`)
  - Minutes after each allowed hour that posting is still permitted.

Failures are appended to `failure_log.jsonl` with `timestamp_jst/stage/message`.
Use `X_DRY_RUN=true` to bypass schedule guard for safe verification.
