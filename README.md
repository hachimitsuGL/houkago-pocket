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
- 公開サイトは Azure App Service の Linux / Python 3.12 で動作します。投稿本文を `/home/data/houkago-pocket` に、写真を非公開の Azure Blob Storage に保存します。以前の写真は初めて開いたときに同じ画像 URL のまま Blob に移行します。
- GitHub Actions が `main` への push を受け、Linux 用の依存関係を含むアプリを作成します。Azure への認証は OIDC を使います。デプロイ後は `/static/release.json` のコミットと投稿一覧 API を確認します。

## Azure に写真を保存する

既存の Web App と同じサブスクリプションを選び、Cloud Shell の Bash でプロジェクトのディレクトリを開きます。`APP` にアプリ名、`RG` にリソースグループ名、`LOCATION` に利用可能なリージョン（例 `japaneast`）を設定します。学校が用意したアプリを使う場合は、対象を確認して `export POCKET_USE_ASSIGNED_APP=1` を設定します。

| コマンド | 投稿本文 | 新規投稿の写真 |
| --- | --- | --- |
| `bash deploy.sh blob` | App Service のファイル。これまでの投稿も引き続き表示 | 非公開コンテナー。アプリ設定は `BLOB_ACCOUNT_NAME` と `BLOB_CONTAINER` |
| `bash deploy.sh storage` | Azure Table。設定を切り替える前の本文は自動移行されません | 同じ非公開コンテナー |

どちらも、必要に応じて指定リージョンに Standard LRS のストレージアカウントとコンテナー `pocketimages` を作成します。LRS は一つのデータセンター内でデータを複製し、保存容量や読み書きの回数などに応じて料金がかかります。[Azure Blob Storage の料金](https://azure.microsoft.com/ja-jp/pricing/details/storage/blobs/)

写真の読み書きには Web App のシステム割り当てマネージド ID を使います。`Storage Blob Data Contributor` の権限は画像コンテナー内に限定します。ストレージの共有キー認証と匿名 Blob アクセスは無効です。写真は掲示板の画像 URL から表示されます。設定後は写真付きの新規投稿を作り、別のブラウザーでも画像が表示されることを確認してください。

## ローカルで起動する

Python 3.12 が必要です。以下は macOS / Linux での実行例です。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m flask --app app run
```

ブラウザーで `http://127.0.0.1:5000` を開きます。
