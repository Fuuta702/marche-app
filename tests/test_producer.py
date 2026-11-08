import pytest

from marche.extensions import db
from marche.models import Product


def product_data(**fields):
    data = {
        "name": "トマト",
        "price": "500",
        "stock": "20",
        "description": "朝採りです",
        "is_published": "y",
    }
    data.update(fields)
    return data


# ---- ロールでの守り ----


def test_出品管理のリンクは生産者にだけ出る(client, customer, producer, login):
    login(customer)
    assert "出品管理" not in client.get("/").text
    client.post("/logout")
    login(producer)
    assert "出品管理" in client.get("/").text


@pytest.mark.parametrize("url", ["/producer/products", "/producer/products/new"])
def test_購入者と管理者は生産者の画面を開けない(client, customer, admin, login, url):
    login(customer)
    assert client.get(url).status_code == 403
    client.post("/logout")
    login(admin)
    assert client.get(url).status_code == 403


def test_未ログインで出品管理を開くとログイン画面へ(client):
    response = client.get("/producer/products")
    assert response.status_code == 302
    assert "/login?next=%2Fproducer%2Fproducts" in response.headers["Location"]


# ---- 一覧 ----


def test_一覧には自分の商品だけが出る(
    client, producer, other_producer, login, make_product
):
    make_product(producer, name="自分のりんご", price=1200)
    make_product(other_producer, name="他人のなし")
    login(producer)
    response = client.get("/producer/products")
    assert response.status_code == 200
    assert "自分のりんご" in response.text
    assert "1,200円" in response.text
    assert "他人のなし" not in response.text


def test_商品が無ければその旨を出す(client, producer, login):
    login(producer)
    assert "まだ商品がありません" in client.get("/producer/products").text


# ---- 登録 ----


def test_商品を登録できる(client, producer, login):
    login(producer)
    assert client.get("/producer/products/new").status_code == 200
    response = client.post("/producer/products/new", data=product_data())
    assert response.status_code == 302
    product = db.session.scalar(db.select(Product))
    assert product.name == "トマト"
    assert product.price == 500
    assert product.stock == 20
    assert product.is_published is True
    assert product.producer_id == producer.id


def test_公開のチェックが無ければ非公開で登録される(client, producer, login):
    login(producer)
    data = product_data()
    del data["is_published"]
    client.post("/producer/products/new", data=data)
    assert db.session.scalar(db.select(Product)).is_published is False


def test_境界の値で登録できる(client, producer, login):
    login(producer)
    response = client.post(
        "/producer/products/new", data=product_data(price="50", stock="0")
    )
    assert response.status_code == 302
    response = client.post(
        "/producer/products/new",
        data=product_data(name="x" * 50, price="100000", stock="999"),
    )
    assert response.status_code == 302
    assert len(db.session.scalars(db.select(Product)).all()) == 2


def test_フォームのproducer_idは無視される(client, producer, other_producer, login):
    login(producer)
    client.post(
        "/producer/products/new", data=product_data(producer_id=other_producer.id)
    )
    assert db.session.scalar(db.select(Product)).producer_id == producer.id


@pytest.mark.parametrize(
    "fields",
    [
        {"price": "49"},
        {"price": "100001"},
        {"price": ""},
        {"price": "abc"},
        {"stock": "-1"},
        {"stock": "1000"},
        {"stock": ""},
        {"name": ""},
        {"name": "x" * 51},
        {"description": "x" * 2001},
    ],
)
def test_不正な入力では登録できない(client, producer, login, fields):
    login(producer)
    response = client.post("/producer/products/new", data=product_data(**fields))
    assert response.status_code == 422
    assert db.session.scalar(db.select(Product)) is None


# ---- 編集 ----


def test_自分の商品を編集できる(client, producer, login, make_product):
    product = make_product(producer)
    before = product.updated_at
    login(producer)
    assert client.get(f"/producer/products/{product.id}/edit").status_code == 200
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(name="新しい名前", price="800"),
    )
    assert response.status_code == 302
    db.session.refresh(product)
    assert product.name == "新しい名前"
    assert product.price == 800
    assert product.updated_at >= before


def test_不正な入力では編集できない(client, producer, login, make_product):
    product = make_product(producer, price=300)
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit", data=product_data(price="10")
    )
    assert response.status_code == 422
    db.session.refresh(product)
    assert product.price == 300


# ---- 切り替えと削除 ----


def test_公開と非公開を切り替えられる(client, producer, login, make_product):
    product = make_product(producer, is_published=False)
    login(producer)
    client.post(f"/producer/products/{product.id}/toggle")
    db.session.refresh(product)
    assert product.is_published is True
    client.post(f"/producer/products/{product.id}/toggle")
    db.session.refresh(product)
    assert product.is_published is False


def test_自分の商品を削除できる(client, producer, login, make_product):
    product = make_product(producer)
    login(producer)
    response = client.post(f"/producer/products/{product.id}/delete")
    assert response.status_code == 302
    assert db.session.get(Product, product.id) is None


@pytest.mark.parametrize("action", ["toggle", "delete"])
def test_切り替えと削除はGETでは使えない(client, producer, login, make_product, action):
    product = make_product(producer)
    login(producer)
    assert client.get(f"/producer/products/{product.id}/{action}").status_code == 405


# ---- 他の生産者の商品 ----


def test_他の生産者の商品は編集できない(
    client, producer, other_producer, login, make_product
):
    product = make_product(other_producer, name="他人の商品")
    login(producer)
    url = f"/producer/products/{product.id}/edit"
    assert client.get(url).status_code == 403
    assert client.post(url, data=product_data(name="乗っ取り")).status_code == 403
    db.session.refresh(product)
    assert product.name == "他人の商品"


def test_他の生産者の商品は切り替えられない(
    client, producer, other_producer, login, make_product
):
    product = make_product(other_producer, is_published=True)
    login(producer)
    assert client.post(f"/producer/products/{product.id}/toggle").status_code == 403
    db.session.refresh(product)
    assert product.is_published is True


def test_他の生産者の商品は削除できない(
    client, producer, other_producer, login, make_product
):
    product = make_product(other_producer)
    login(producer)
    assert client.post(f"/producer/products/{product.id}/delete").status_code == 403
    assert db.session.get(Product, product.id) is not None


@pytest.mark.parametrize("action", ["edit", "toggle", "delete"])
def test_存在しない商品は404(client, producer, login, action):
    login(producer)
    assert client.post(f"/producer/products/9999/{action}").status_code == 404
