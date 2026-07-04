# -*- coding: utf-8 -*-
"""
Google Trendsからニッチ関連のトレンドタグを取得する共通モジュール。
Google Trends取得に失敗しても、JSTの曜日・季節・コミュニティタグから
「トレンドを意識したタグ」を必ず返す（絶対に空にならない）。
"""
import random
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

# 曜日連動タグ（X上で定番のモーメント系ハッシュタグ）
WEEKDAY_TAGS = {
    0: ["MondayMotivation", "月曜日の積み上げ"],
    1: ["TransformationTuesday"],
    2: ["WednesdayWorkout"],
    3: ["ThursdayVibes"],
    4: ["FlexFriday", "金曜日"],
    5: ["WeekendWorkout", "土曜日"],
    6: ["SundayReset"],
}

# 季節連動タグ（月で切り替え）
SEASONAL_TAGS = {
    "spring": ["春トレ", "新生活"],
    "summer": ["夏ボディ", "summerbody"],
    "autumn": ["スポーツの秋", "秋トレ"],
    "winter": ["冬トレ", "バルクアップ"],
}

# コミュニティ系タグ（日本語圏の筋トレクラスタで回っている定番）
COMMUNITY_TAGS = [
    "筋トレ好きと繋がりたい",
    "筋トレ女子",
    "フィットネス女子",
    "ジム女子",
    "腹筋女子",
    "筋トレ初心者",
]


def get_fallback_trend_tags(max_tags=5):
    """外部API不要のトレンド風タグ（曜日1-2 + 季節1 + コミュニティ2）。"""
    now = datetime.now(JST)
    if now.month in (3, 4, 5):
        season = "spring"
    elif now.month in (6, 7, 8):
        season = "summer"
    elif now.month in (9, 10, 11):
        season = "autumn"
    else:
        season = "winter"

    tags = list(WEEKDAY_TAGS.get(now.weekday(), []))
    tags.append(random.choice(SEASONAL_TAGS[season]))
    tags.extend(random.sample(COMMUNITY_TAGS, min(2, len(COMMUNITY_TAGS))))
    random.shuffle(tags)
    return tags[:max_tags]

# トレンド取得に使うシードキーワード（自分のニッチ）
SEED_KEYWORDS = [
    'muscle girl',
    'female bodybuilder',
    'fitness motivation',
    'gym workout',
    'strong women',
]

# トレンドとして拾っても無関係なものを除外するフィルタ
RELEVANCE_KEYWORDS = {
    'muscle', 'fitness', 'gym', 'workout', 'bodybuilding', 'strong',
    'fit', 'training', 'exercise', 'physique', 'flex', 'gains',
    'bicep', 'abs', 'squat', 'deadlift', 'bench', 'crossfit',
    'yoga', 'pilates', 'cardio', 'protein', 'bulk', 'shred',
    'ripped', 'lean', 'athletic', 'fbb', 'ifbb', 'npc',
    'bodybuilder', 'powerlifting', 'weightlifting', 'calisthenics',
    '筋トレ', '筋肉', 'フィットネス', 'ジム', 'トレーニング',
}

WEAK_TREND_KEYWORDS = {
    'quote', 'quotes', 'saying', 'sayings', 'mother', 'mothersday',
    'mom', 'movie', 'film', 'book', 'song', 'lyrics',
}


def get_trending_tags(max_tags=5):
    """
    Google Trendsから関連トレンドタグを取得する。
    取得できない場合は曜日・季節ベースのフォールバックタグを返す（空にしない）。
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        print("pytrends not installed, using fallback trend tags")
        return get_fallback_trend_tags(max_tags)

    trending_tags = []

    try:
        pytrends = TrendReq(hl='en-US', tz=360, timeout=(10, 25))

        # ランダムにシードキーワードを2つ選んで関連クエリを取得
        seeds = random.sample(SEED_KEYWORDS, min(2, len(SEED_KEYWORDS)))
        print(f"Fetching trends for: {seeds}")

        pytrends.build_payload(seeds, cat=0, timeframe='now 7-d', geo='', gprop='')

        # 関連クエリ（rising = 急上昇）を取得
        related = pytrends.related_queries()
        for keyword in seeds:
            data = related.get(keyword, {})

            # rising（急上昇）から取得
            rising = data.get('rising')
            if rising is not None and not rising.empty:
                for _, row in rising.head(10).iterrows():
                    query = row['query'].strip().lower()
                    # 自分のニッチに関連あるかチェック
                    if _is_relevant(query):
                        tag = query.replace(' ', '')
                        trending_tags.append(tag)

            # top（定番人気）からも取得
            top = data.get('top')
            if top is not None and not top.empty:
                for _, row in top.head(5).iterrows():
                    query = row['query'].strip().lower()
                    if _is_relevant(query):
                        tag = query.replace(' ', '')
                        trending_tags.append(tag)

        # 重複除去してシャッフル
        seen = set()
        unique = []
        for t in trending_tags:
            if t.lower() not in seen:
                seen.add(t.lower())
                unique.append(t)
        random.shuffle(unique)

        result = unique[:max_tags]
        if result:
            print(f"Trending tags found: {result}")
            return result
        print("No relevant trending tags found, using fallback trend tags")
        return get_fallback_trend_tags(max_tags)

    except Exception as e:
        print(f"Trend fetch failed (non-fatal): {e}, using fallback trend tags")
        return get_fallback_trend_tags(max_tags)


def _is_relevant(query):
    """クエリが自分のニッチに関連あるかチェック"""
    query_lower = query.lower()
    if any(kw in query_lower.replace(' ', '') for kw in WEAK_TREND_KEYWORDS):
        return False
    return any(kw in query_lower for kw in RELEVANCE_KEYWORDS)
