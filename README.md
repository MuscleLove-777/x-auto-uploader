# X Auto Uploader

Google Driveの動画をX (Twitter)に自動投稿するシステム。
GitHub Actionsで1日4回（JST 6:00/12:00/18:00/24:00）自動実行。

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
- 動画は最大140秒・512MB
- MP4/MOV形式のみ
- 読み取りAPI（タイムライン取得等）はほぼ使用不可
