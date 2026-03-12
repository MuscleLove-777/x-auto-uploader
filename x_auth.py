# -*- coding: utf-8 -*-
"""
X (Twitter) OAuth認証ヘルパー
初回のアクセストークン取得用スクリプト（ローカルで1回実行）
"""
import json
import os
from requests_oauthlib import OAuth1Session

CREDENTIALS_FILE = "x_credentials.json"


def authenticate():
    """OAuth 1.0a 3-legged認証フローでアクセストークンを取得"""
    consumer_key = input("Consumer Key (API Key): ").strip()
    consumer_secret = input("Consumer Secret (API Secret): ").strip()

    # Step 1: Request Token
    request_token_url = "https://api.twitter.com/oauth/request_token"
    oauth = OAuth1Session(consumer_key, client_secret=consumer_secret, callback_uri="oob")
    resp = oauth.fetch_request_token(request_token_url)
    owner_key = resp.get("oauth_token")
    owner_secret = resp.get("oauth_token_secret")

    # Step 2: ユーザー認証
    auth_url = f"https://api.twitter.com/oauth/authorize?oauth_token={owner_key}"
    print(f"\n以下のURLをブラウザで開いて認証してください:\n{auth_url}\n")
    verifier = input("PINコードを入力: ").strip()

    # Step 3: Access Token
    access_token_url = "https://api.twitter.com/oauth/access_token"
    oauth = OAuth1Session(
        consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=owner_key,
        resource_owner_secret=owner_secret,
        verifier=verifier,
    )
    resp = oauth.fetch_access_token(access_token_url)
    access_token = resp.get("oauth_token")
    access_token_secret = resp.get("oauth_token_secret")
    screen_name = resp.get("screen_name", "")

    credentials = {
        "consumer_key": consumer_key,
        "consumer_secret": consumer_secret,
        "access_token": access_token,
        "access_token_secret": access_token_secret,
        "screen_name": screen_name,
    }

    with open(CREDENTIALS_FILE, 'w') as f:
        json.dump(credentials, f, indent=2)

    print(f"\n認証成功！ ユーザー: @{screen_name}")
    print(f"認証情報を {CREDENTIALS_FILE} に保存しました。")
    print("\n以下をGitHub Secretsに設定してください:")
    print(f"  X_CONSUMER_KEY     = {consumer_key}")
    print(f"  X_CONSUMER_SECRET  = {consumer_secret}")
    print(f"  X_ACCESS_TOKEN     = {access_token}")
    print(f"  X_ACCESS_TOKEN_SECRET = {access_token_secret}")

    return credentials


if __name__ == '__main__':
    authenticate()
