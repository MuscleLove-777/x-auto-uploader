# -*- coding: utf-8 -*-
"""
X (Twitter) 動画ランダムアップロード（GitHub Actions用）
Google Driveからダウンロード → ランダム1本アップロード → アップロード済みを記録
Free Tier対応: 月500ツイート / 動画最大140秒・512MB
"""
import sys
import json
import os
import random
import time
import hashlib
from datetime import datetime, timezone, timedelta
import requests
from requests_oauthlib import OAuth1

JST = timezone(timedelta(hours=9))

# 金曜21時JST → 専用フォルダ、それ以外 → デフォルトフォルダ
GDRIVE_FOLDER_ID_FRIDAY = os.environ.get("GDRIVE_FOLDER_ID_FRIDAY", "")
GDRIVE_FOLDER_ID_DEFAULT = os.environ.get("GDRIVE_FOLDER_ID_DEFAULT", "")


def get_gdrive_folder_id():
    """現在時刻（JST）に応じてGoogle DriveフォルダIDを選択"""
    now_jst = datetime.now(JST)
    is_friday_21 = (now_jst.weekday() == 4 and now_jst.hour == 21)
    if is_friday_21 and GDRIVE_FOLDER_ID_FRIDAY:
        print(f"金曜21時モード: Friday専用フォルダを使用")
        return GDRIVE_FOLDER_ID_FRIDAY
    else:
        print(f"通常モード: デフォルトフォルダを使用 (JST {now_jst.strftime('%A %H:%M')})")
        return GDRIVE_FOLDER_ID_DEFAULT
PATREON_LINK = "https://www.patreon.com/cw/MuscleLove"
VIDEO_EXTENSIONS = {'.mp4', '.mov'}
MAX_FILE_SIZE = 512 * 1024 * 1024
MAX_DURATION_SEC = 140
UPLOADED_LOG = "uploaded.json"
MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
TWEET_URL = "https://api.x.com/2/tweets"

# --- タグマッピング ---
CONTENT_TAG_MAP = {
    'training': ['筋トレ', 'workout', 'training', 'gym', 'fitness'],
    'workout': ['筋トレ', 'workout', 'training', 'gym', 'fitness'],
    'pullups': ['懸垂', 'pullups', 'backworkout', 'calisthenics'],
    'posing': ['ポージング', 'posing', 'bodybuilding', 'physique'],
    'flex': ['フレックス', 'flex', 'muscle', 'bodybuilding'],
    'muscle': ['筋肉', 'muscle', 'muscular', 'fitness'],
    'bicep': ['上腕二頭筋', 'biceps', 'arms', 'muscle'],
    'abs': ['腹筋', 'abs', 'sixpack', 'core'],
    'leg': ['脚トレ', 'legs', 'quads', 'legday'],
    'back': ['背中', 'back', 'lats', 'backday'],
    'squat': ['スクワット', 'squat', 'legs', 'legday'],
    'deadlift': ['デッドリフト', 'deadlift', 'powerlifting'],
    'bench': ['ベンチプレス', 'benchpress', 'chest'],
    'bikini': ['ビキニ', 'bikini', 'bikinifitness', 'figurecompetitor'],
    'competition': ['大会', 'competition', 'bodybuilding', 'contest'],
    'nsfw': ['nsfw', 'sexy', 'hotfit'],
    'sexy': ['nsfw', 'sexy', 'hotfit'],
    'adult': ['nsfw', 'adult', 'hotfit'],
}

BASE_TAGS = [
    'musclegirl', 'muscularwoman', 'femalemuscle', 'strongwomen',
    'fbb', 'fitnessmotivation', 'gymgirl', '筋肉女子', '筋トレ女子', 'fitfam',
]

# NSFWを判定するキーワード
NSFW_KEYWORDS = {'nsfw', 'sexy', 'adult', 'nude', 'bikini', 'erotic', 'hot', 'エロ'}

# ツイート本文テンプレート（ランダム選択）
TWEET_TEMPLATES = [
    "💪 {category}\n\n{hashtags}\n\n🔥 {patreon}",
    "🔥 {category}\n\n{hashtags}\n\n💪 {patreon}",
    "{category} 💪✨\n\n{hashtags}\n\n{patreon}",
    "✨ {category}\n\n{hashtags}\n\n🔥 More → {patreon}",
]


def get_oauth():
    """OAuth1認証オブジェクトを取得"""
    return OAuth1(
        os.environ.get("X_CONSUMER_KEY", ""),
        os.environ.get("X_CONSUMER_SECRET", ""),
        os.environ.get("X_ACCESS_TOKEN", ""),
        os.environ.get("X_ACCESS_TOKEN_SECRET", ""),
    )


def load_uploaded_log():
    if os.path.exists(UPLOADED_LOG):
        with open(UPLOADED_LOG, 'r') as f:
            return json.load(f)
    return []


def save_uploaded_log(log):
    with open(UPLOADED_LOG, 'w') as f:
        json.dump(log, f, indent=2)


def download_videos():
    """Google Driveからダウンロード"""
    import gdown
    dl_dir = "videos"
    os.makedirs(dl_dir, exist_ok=True)
    folder_id = get_gdrive_folder_id()
    if not folder_id:
        print("Error: GDRIVE_FOLDER_ID not set")
        return []
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    print(f"Downloading from Google Drive: {url}")
    try:
        gdown.download_folder(url, output=dl_dir, quiet=False, remaining_ok=True)
    except Exception as e:
        print(f"Download error: {e}")

    files = []
    for root, dirs, filenames in os.walk(dl_dir):
        for fname in filenames:
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext in VIDEO_EXTENSIONS:
                size = os.path.getsize(fpath)
                if size <= MAX_FILE_SIZE:
                    files.append(fpath)
    return files


def is_nsfw(video_path):
    """ファイルパスからNSFWかどうかを判定"""
    path_lower = video_path.lower().replace('\\', '/').replace('-', ' ').replace('_', ' ')
    return any(kw in path_lower for kw in NSFW_KEYWORDS)


def generate_tags(video_path):
    """ファイルパスからハッシュタグを生成"""
    tags = list(BASE_TAGS)
    path_lower = video_path.lower().replace('\\', '/').replace('-', ' ').replace('_', ' ')
    matched = set()
    for keyword, keyword_tags in CONTENT_TAG_MAP.items():
        if keyword in path_lower:
            for t in keyword_tags:
                if t not in matched:
                    tags.append(t)
                    matched.add(t)
    seen = set()
    unique_tags = []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique_tags.append(t)
    return unique_tags


def build_tweet_text(video_path, tags):
    """ツイート本文を生成"""
    parts = video_path.replace('\\', '/').split('/')
    category = "Muscle"
    for p in parts:
        if p not in ['videos', ''] and '.' not in p:
            category = p
            break
    hashtags = ' '.join([f'#{t}' for t in tags[:15]])
    template = random.choice(TWEET_TEMPLATES)
    return template.format(
        category=category,
        hashtags=hashtags,
        patreon=PATREON_LINK,
    )


# --- メディアアップロード（chunked） ---

def upload_media_init(auth, file_size, media_type="video/mp4"):
    """INIT: チャンクアップロード開始"""
    resp = requests.post(
        MEDIA_UPLOAD_URL,
        data={
            "command": "INIT",
            "total_bytes": file_size,
            "media_type": media_type,
            "media_category": "tweet_video",
        },
        auth=auth,
    )
    resp.raise_for_status()
    media_id = resp.json()["media_id_string"]
    print(f"INIT OK: media_id={media_id}")
    return media_id


def upload_media_append(auth, media_id, file_path, chunk_size=4 * 1024 * 1024):
    """APPEND: ファイルをチャンクで送信"""
    segment = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            resp = requests.post(
                MEDIA_UPLOAD_URL,
                data={
                    "command": "APPEND",
                    "media_id": media_id,
                    "segment_index": segment,
                },
                files={"media_data": chunk},
                auth=auth,
            )
            resp.raise_for_status()
            print(f"APPEND segment {segment} OK")
            segment += 1
    return segment


def upload_media_finalize(auth, media_id):
    """FINALIZE: アップロード完了"""
    resp = requests.post(
        MEDIA_UPLOAD_URL,
        data={
            "command": "FINALIZE",
            "media_id": media_id,
        },
        auth=auth,
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"FINALIZE OK: {result}")
    return result


def wait_for_processing(auth, media_id, max_wait=300):
    """動画の処理完了を待つ"""
    elapsed = 0
    while elapsed < max_wait:
        resp = requests.get(
            MEDIA_UPLOAD_URL,
            params={
                "command": "STATUS",
                "media_id": media_id,
            },
            auth=auth,
        )
        resp.raise_for_status()
        info = resp.json()
        state = info.get("processing_info", {}).get("state", "")
        if state == "succeeded":
            print("Processing complete!")
            return True
        elif state == "failed":
            error = info.get("processing_info", {}).get("error", {})
            print(f"Processing failed: {error}")
            return False
        wait_sec = info.get("processing_info", {}).get("check_after_secs", 5)
        print(f"Processing... state={state}, waiting {wait_sec}s")
        time.sleep(wait_sec)
        elapsed += wait_sec
    print("Processing timeout!")
    return False


def upload_video(auth, file_path):
    """動画をXにアップロード（chunked upload）"""
    file_size = os.path.getsize(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    media_type = "video/mp4" if ext == ".mp4" else "video/quicktime"

    media_id = upload_media_init(auth, file_size, media_type)
    upload_media_append(auth, media_id, file_path)
    result = upload_media_finalize(auth, media_id)

    if "processing_info" in result:
        if not wait_for_processing(auth, media_id):
            return None
    return media_id


def post_tweet(auth, text, media_id, possibly_sensitive=False):
    """ツイートを投稿"""
    payload = {
        "text": text,
        "media": {
            "media_ids": [media_id],
        },
    }
    if possibly_sensitive:
        payload["possibly_sensitive"] = True

    resp = requests.post(
        TWEET_URL,
        json=payload,
        auth=auth,
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"Tweet posted! id={result['data']['id']}")
    return result


def main():
    auth = get_oauth()

    # 認証チェック
    consumer_key = os.environ.get("X_CONSUMER_KEY", "")
    access_token = os.environ.get("X_ACCESS_TOKEN", "")
    if not all([consumer_key, access_token]):
        print("Error: Missing X API credentials")
        return 1

    print("Auth credentials loaded.")

    # Google Driveからダウンロード
    videos = download_videos()
    if not videos:
        print("No videos found!")
        return 0

    # 未アップロード動画をフィルタ
    uploaded_log = load_uploaded_log()
    available = [v for v in videos if os.path.basename(v) not in uploaded_log]
    if not available:
        print("All videos already uploaded!")
        return 0

    print(f"\nAvailable: {len(available)} / Total: {len(videos)}")
    video = random.choice(available)
    fname = os.path.basename(video)
    print(f"Selected: {fname}")

    # タグ生成 & ツイート本文作成
    tags = generate_tags(video)
    tweet_text = build_tweet_text(video, tags)
    sensitive = is_nsfw(video)
    print(f"Tags: {', '.join(tags[:10])}...")
    print(f"NSFW: {sensitive}")
    print(f"Tweet:\n{tweet_text}\n")

    # 動画アップロード
    try:
        print("Uploading video...")
        media_id = upload_video(auth, video)
        if not media_id:
            print("Video upload/processing failed!")
            return 1

        # ツイート投稿
        print("Posting tweet...")
        result = post_tweet(auth, tweet_text, media_id, possibly_sensitive=sensitive)

        # 成功 → ログ保存
        uploaded_log.append(fname)
        save_uploaded_log(uploaded_log)
        print(f"Success! Remaining: {len(available) - 1}")
        return 0

    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}")
        print(f"Response: {e.response.text if e.response else 'N/A'}")
        return 1
    except Exception as e:
        print(f"Upload error: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
