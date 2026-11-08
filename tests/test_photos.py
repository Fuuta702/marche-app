import io
import os

import pytest

from marche.extensions import db
from marche.models import Product
from marche.uploads import delete_image, detect_media_type, save_image

# 中身の判定は先頭のバイトしか見ないので、マジックナンバーで始まる短いデータで足りる
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 20


def upload_folder(app):
    return app.config["UPLOAD_FOLDER"]


def saved_files(app):
    folder = upload_folder(app)
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def product_data(photo=None, **fields):
    data = {"name": "トマト", "price": "500", "stock": "20", "description": ""}
    data.update(fields)
    if photo is not None:
        content, filename = photo
        data["photo"] = (io.BytesIO(content), filename)
    return data


# ---- uploads.py ----


@pytest.mark.parametrize(
    "data, expected",
    [
        (JPEG, "image/jpeg"),
        (PNG, "image/png"),
        (WEBP, "image/webp"),
        (b"hello, world", None),
        (b"GIF89a" + b"\x00" * 20, None),
        (b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 20, None),  # RIFF でも WebP ではない
        (b"", None),
    ],
)
def test_先頭のバイトで画像の種類を判定する(data, expected):
    assert detect_media_type(data) == expected


@pytest.mark.parametrize(
    "data, media_type, ext",
    [
        (JPEG, "image/jpeg", ".jpg"),
        (PNG, "image/png", ".png"),
        (WEBP, "image/webp", ".webp"),
    ],
)
def test_UUIDの名前で保存する(app, data, media_type, ext):
    filename = save_image(data, media_type)
    stem, actual_ext = os.path.splitext(filename)
    assert actual_ext == ext
    assert len(stem) == 32  # uuid4().hex
    with open(os.path.join(upload_folder(app), filename), "rb") as f:
        assert f.read() == data


def test_写真を消せる_無くてもエラーにならない(app):
    filename = save_image(PNG, "image/png")
    delete_image(filename)
    assert saved_files(app) == []
    delete_image(filename)  # 2回目（もう無い）
    delete_image(None)


def test_フォルダーの外のファイルは消さない(app, tmp_path):
    outside = tmp_path / "important.txt"
    outside.write_text("消してはいけない")
    delete_image("../important.txt")
    assert outside.exists()


# ---- 登録 ----


def test_写真付きで登録できる(app, client, producer, login):
    login(producer)
    response = client.post(
        "/producer/products/new", data=product_data(photo=(PNG, "tomato.png"))
    )
    assert response.status_code == 302
    product = db.session.scalar(db.select(Product))
    assert product.image_filename.endswith(".png")
    assert "tomato" not in product.image_filename  # 元のファイル名は使わない
    assert saved_files(app) == [product.image_filename]


def test_写真なしでも登録できる(app, client, producer, login):
    login(producer)
    response = client.post("/producer/products/new", data=product_data())
    assert response.status_code == 302
    assert db.session.scalar(db.select(Product)).image_filename is None
    assert saved_files(app) == []


def test_拡張子と中身が違っても中身に合わせて保存する(app, client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(photo=(JPEG, "a.png")))
    assert db.session.scalar(db.select(Product)).image_filename.endswith(".jpg")


@pytest.mark.parametrize(
    "photo",
    [
        (b"<?php echo 'x'; ?>", "evil.jpg"),  # 中身が画像ではない
        (b"GIF89a" + b"\x00" * 20, "anime.gif"),  # 拡張子が許可されていない
        (PNG, "photo.txt"),  # 中身は PNG でも拡張子が違う
        (PNG + b"\x00" * (5 * 1024 * 1024), "big.png"),  # 5MB を超える
    ],
)
def test_不正な写真では登録できない(app, client, producer, login, photo):
    login(producer)
    response = client.post("/producer/products/new", data=product_data(photo=photo))
    assert response.status_code == 422
    assert db.session.scalar(db.select(Product)) is None
    assert saved_files(app) == []


def test_画像でなければエラーを出す(client, producer, login):
    login(producer)
    response = client.post(
        "/producer/products/new", data=product_data(photo=(b"not image", "a.jpg"))
    )
    assert "画像ではないファイルです" in response.text


def test_6MBを超えると413(app, client, producer, login):
    login(producer)
    big = PNG + b"\x00" * (6 * 1024 * 1024)
    response = client.post(
        "/producer/products/new", data=product_data(photo=(big, "big.png"))
    )
    assert response.status_code == 413
    assert "ファイルが大きすぎます" in response.text
    assert saved_files(app) == []


# ---- 編集と削除 ----


def make_product_with_photo(app, make_product, producer):
    with app.test_request_context():
        filename = save_image(PNG, "image/png")
    return make_product(producer, image_filename=filename)


def test_編集画面に今の写真が出る(app, client, producer, login, make_product):
    product = make_product_with_photo(app, make_product, producer)
    login(producer)
    response = client.get(f"/producer/products/{product.id}/edit")
    assert f"/static/uploads/{product.image_filename}" in response.text


def test_写真を差し替えると古い写真が消える(app, client, producer, login, make_product):
    product = make_product_with_photo(app, make_product, producer)
    old = product.image_filename
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(photo=(JPEG, "new.jpg")),
    )
    assert response.status_code == 302
    db.session.refresh(product)
    assert product.image_filename != old
    assert saved_files(app) == [product.image_filename]


def test_写真を選ばずに編集すると今の写真が残る(
    app, client, producer, login, make_product
):
    product = make_product_with_photo(app, make_product, producer)
    old = product.image_filename
    login(producer)
    client.post(
        f"/producer/products/{product.id}/edit", data=product_data(name="新しい名前")
    )
    db.session.refresh(product)
    assert product.image_filename == old
    assert saved_files(app) == [old]


def test_不正な写真で編集すると今の写真が残る(
    app, client, producer, login, make_product
):
    product = make_product_with_photo(app, make_product, producer)
    old = product.image_filename
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(photo=(b"not image", "a.jpg")),
    )
    assert response.status_code == 422
    db.session.refresh(product)
    assert product.image_filename == old
    assert saved_files(app) == [old]


def test_商品を削除すると写真も消える(app, client, producer, login, make_product):
    product = make_product_with_photo(app, make_product, producer)
    login(producer)
    client.post(f"/producer/products/{product.id}/delete")
    assert saved_files(app) == []


def test_他の生産者の商品には写真を送れない(
    app, client, producer, other_producer, login, make_product
):
    product = make_product(other_producer)
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit", data=product_data(photo=(PNG, "a.png"))
    )
    assert response.status_code == 403
    assert saved_files(app) == []
