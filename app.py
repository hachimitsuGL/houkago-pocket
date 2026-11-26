"""Flask entry point for 放課後ポケット."""

from datetime import datetime, timezone
from io import BytesIO
import time
from uuid import uuid4

from flask import Flask, jsonify, request, send_file
from PIL import Image, ImageOps
from werkzeug.exceptions import HTTPException

from board_storage import ID_PATTERN, get_board

app = Flask(__name__)
app.json.ensure_ascii = False
MAX_PHOTO_BYTES = 3 * 1024 * 1024
MAX_PIXELS = 20_000_000
app.config.update(
    MAX_CONTENT_LENGTH=MAX_PHOTO_BYTES + 128 * 1024,
    MAX_FORM_MEMORY_SIZE=128 * 1024,
    MAX_FORM_PARTS=12,
)


class InvalidPost(ValueError):
    pass


def text_field(name, label, limit, required=True):
    value = request.form.get(name, "").strip()
    if required and not value:
        raise InvalidPost(f"{label}を入力してください。")
    if len(value) > limit:
        raise InvalidPost(f"{label}は{limit}文字以内で入力してください。")
    return value


def read_photo(upload):
    if upload is None or not upload.filename:
        return None
    raw = upload.stream.read(MAX_PHOTO_BYTES + 1)
    if len(raw) > MAX_PHOTO_BYTES:
        raise InvalidPost("画像は3 MB以内で選んでください。")
    try:
        with Image.open(BytesIO(raw), formats=("JPEG", "PNG", "WEBP")) as source:
            if source.width * source.height > MAX_PIXELS:
                raise InvalidPost("画像は2000万画素以内で選んでください。")
            if getattr(source, "is_animated", False):
                raise InvalidPost("アニメーション画像は投稿できません。静止画を選んでください。")
            source.load()
            oriented = ImageOps.exif_transpose(source)
            oriented.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
            rgba = oriented.convert("RGBA")
            # Rebuild from pixels so that location, EXIF and other metadata are discarded.
            clean = Image.new("RGB", rgba.size, "white")
            clean.paste(rgba, mask=rgba.getchannel("A"))
            output = BytesIO()
            clean.save(output, format="JPEG", quality=83)
            return output.getvalue()
    except InvalidPost:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise InvalidPost("読み込めるJPEG・PNG・WebPの画像を選んでください。") from None


@app.after_request
def response_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.path == "/api/posts":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(InvalidPost)
def invalid_post(error):
    return jsonify(error=str(error)), 400


@app.errorhandler(HTTPException)
def http_error(error):
    if not request.path.startswith("/api/"):
        return error
    message = {
        400: "入力内容を確認してください。",
        404: "見つかりませんでした。",
        405: "この操作は利用できません。",
        413: "送信データが大きすぎます。画像は3 MB以内で選んでください。",
    }.get(error.code, "リクエストを処理できませんでした。")
    response = error.get_response()
    response.set_data(app.json.dumps({"error": message}))
    response.content_type = "application/json"
    return response


@app.errorhandler(Exception)
def unavailable(error):
    app.logger.exception("The board request failed.")
    return jsonify(error="掲示板のデータを利用できません。時間をおいてもう一度お試しください。"), 503


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.get("/api/posts")
def posts():
    return jsonify(posts=get_board().list_posts())


@app.post("/api/posts")
def create_post():
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return jsonify(error="掲示板の投稿画面から送信してください。"), 403

    title = text_field("title", "タイトル", 40)
    body = text_field("body", "本文", 240)
    alias = text_field("alias", "ニックネーム", 12, required=False) or "匿名さん"
    minutes = request.form.get("minutes", "")
    category = request.form.get("category", "")
    if minutes not in ("5", "15", "30"):
        raise InvalidPost("所要時間は5分・15分・30分から選んでください。")
    if category not in ("勉強", "部活", "息抜き"):
        raise InvalidPost("カテゴリは勉強・部活・息抜きから選んでください。")
    photo = read_photo(request.files.get("image"))
    now_ns = time.time_ns()
    reverse_time = 9_999_999_999_999_999_999 - now_ns
    post_id = f"{reverse_time:019d}-{uuid4().hex}"
    post = {
        "id": post_id,
        "title": title,
        "body": body,
        "minutes": int(minutes),
        "category": category,
        "alias": alias,
        "createdAt": datetime.fromtimestamp(now_ns / 1e9, timezone.utc).isoformat(),
        "imageUrl": f"/api/images/{post_id}" if photo is not None else None,
    }
    get_board().save(post, photo)
    return jsonify(post=post), 201


@app.get("/api/images/<post_id>")
def image(post_id):
    if ID_PATTERN.fullmatch(post_id):
        try:
            data = get_board().image(post_id)
            return send_file(BytesIO(data), mimetype="image/jpeg", max_age=3600)
        except FileNotFoundError:
            pass
    return jsonify(error="画像が見つかりませんでした。"), 404


if __name__ == "__main__":
    app.run()
