import os
import uuid

from flask import current_app

EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def detect_media_type(data: bytes) -> str | None:
    """先頭のバイト（マジックナンバー）で JPEG / PNG / WebP を判定する。どれでもなければ None"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def save_image(data: bytes, media_type: str) -> str:
    """UUID のファイル名で UPLOAD_FOLDER に保存し、ファイル名を返す。元のファイル名は使わない"""
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    filename = uuid.uuid4().hex + EXTENSIONS[media_type]
    with open(os.path.join(folder, filename), "wb") as f:
        f.write(data)
    return filename


def delete_image(filename: str | None) -> None:
    """写真を消す。無くてもエラーにしない"""
    if not filename:
        return
    # "../" などで UPLOAD_FOLDER の外を消さないように、ファイル名の部分だけを使う
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.basename(filename))
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
