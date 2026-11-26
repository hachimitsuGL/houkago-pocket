# 放課後ポケット

バスが来るまであと15分。そんなときに、高校生が短い空き時間の過ごし方を探し、自分のアイデアも紹介できる掲示板です。

[掲示板を開く](https://sk21-pocket-k7m4-dahugce5heh2d5gw.japaneast-01.azurewebsites.net/) · [要件定義を読む](requirements.md) · [自動デプロイの実行履歴](https://github.com/hachimitsuGL/houkago-pocket/actions/workflows/deploy.yml)

## 使い方

1. 残り時間を 5・15・30 分から選びます。15 分を選ぶと、5 分と 15 分のアイデアが表示されます。
2. 「勉強」「部活」「息抜き」で絞り込みます。初めて開いたときは、投稿例を6件読めます。
3. 「投稿する」からタイトル・本文・所要時間・カテゴリを入力します。ニックネームと写真は任意です。写真は JPEG・PNG・WebP の静止画を1枚、3 MiB まで添付できます。

## 構成

- `static/` にある HTML、CSS、JavaScript が一覧と投稿フォームを表示します。
- Flask が `GET /api/posts` と `POST /api/posts` を提供します。写真から位置情報などのメタデータを取り除いて保存します。
- Azure App Service の Linux / Python 3.12 で動作します。既定では、投稿と写真を `/home/data/houkago-pocket` に保存します。Azure Storage を設定すると、本文は Table、写真は非公開の Blob に保存されます。
- GitHub Actions が `main` への push を受け、Linux 用の依存関係を含むアプリを作成します。Azure への認証は OIDC を使います。デプロイ後は `/static/release.json` のコミットと投稿一覧 API を確認します。

## ローカルで起動する

Python 3.12 が必要です。以下は macOS / Linux での実行例です。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m flask --app app run
```

ブラウザーで `http://127.0.0.1:5000` を開きます。
