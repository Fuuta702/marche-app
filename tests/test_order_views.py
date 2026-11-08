import pytest

from marche.extensions import db
from marche.models import Order, OrderStatus


def set_cart(client, cart):
    with client.session_transaction() as sess:
        sess["cart"] = cart


def cart_of(client):
    with client.session_transaction() as sess:
        return dict(sess.get("cart", {}))


def orders():
    return db.session.scalars(db.select(Order)).all()


@pytest.fixture
def apple(producer, make_product):
    return make_product(producer, name="りんご", price=1200, stock=5)


# ---- 注文する ----


def test_注文するとカートが空になり注文詳細へ移る(client, customer, login, apple):
    login(customer)
    set_cart(client, {str(apple.id): 2})
    response = client.post("/orders/")
    order = orders()[0]
    assert response.status_code == 302
    assert response.headers["Location"] == f"/orders/{order.id}"
    assert order.user_id == customer.id
    assert order.status == OrderStatus.PENDING
    assert order.total_amount == 2400
    assert cart_of(client) == {}


def test_未ログインでは注文できずログイン画面へ(client, apple):
    set_cart(client, {str(apple.id): 1})
    response = client.post("/orders/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
    assert orders() == []


def test_空のカートでは注文できない(client, customer, login):
    login(customer)
    response = client.post("/orders/", follow_redirects=True)
    assert "カートが空です" in response.text
    assert orders() == []


def test_在庫が足りなければメッセージを出してカートに戻る(
    client, customer, login, apple
):
    login(customer)
    set_cart(client, {str(apple.id): 6})  # セッションを書き換えて在庫 5 を超える数
    response = client.post("/orders/")
    assert response.headers["Location"] == "/cart/"
    assert orders() == []
    text = client.get("/cart/").text
    assert "在庫が足りません" in text
    assert cart_of(client) == {str(apple.id): 6}  # カートは残す


def test_注文の金額はDBの価格から計算する(client, customer, login, apple):
    login(customer)
    set_cart(client, {str(apple.id): 1})
    # 金額をフォームに混ぜても使わない
    client.post("/orders/", data={"total_amount": "1", "price": "1"})
    assert orders()[0].total_amount == 1200


def test_注文はGETでは作れない(client, customer, login):
    login(customer)
    assert client.get("/orders/").status_code == 405


# ---- 注文詳細 ----


def test_注文詳細に状態と明細と合計が出る(client, customer, login, apple, make_order):
    order = make_order(customer, [(apple, 2)])
    apple.name = "名前を変えたりんご"
    db.session.commit()
    login(customer)
    response = client.get(f"/orders/{order.id}")
    assert response.status_code == 200
    assert "支払い待ち" in response.text
    assert "りんご" in response.text
    assert "名前を変えたりんご" not in response.text  # 注文時点の商品名
    assert "1,200円" in response.text
    assert "2,400円" in response.text
    assert (
        "月" in response.text and "日(" in response.text
    )  # 「11月14日(土) 10:00」の形


def test_他人の注文の詳細は403(client, customer, producer, login, apple, make_order):
    order = make_order(customer, [(apple, 1)])
    login(producer)
    assert client.get(f"/orders/{order.id}").status_code == 403


def test_管理者は他人の注文の詳細を見られる(
    client, customer, admin, login, apple, make_order
):
    order = make_order(customer, [(apple, 1)])
    login(admin)
    assert client.get(f"/orders/{order.id}").status_code == 200


def test_未ログインで注文詳細を開くとログイン画面へ(
    client, customer, apple, make_order
):
    order = make_order(customer, [(apple, 1)])
    response = client.get(f"/orders/{order.id}")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


def test_存在しない注文は404(client, customer, login):
    login(customer)
    assert client.get("/orders/9999").status_code == 404


# ---- 日時の表示 ----


def test_datetime_jaフィルター(app):
    from datetime import datetime

    # DB と同じく、日本時間をタイムゾーンなしで持つ
    datetime_ja = app.jinja_env.filters["datetime_ja"]
    assert datetime_ja(datetime.fromisoformat("2026-11-14T10:00")) == (
        "11月14日(土) 10:00"
    )
    assert datetime_ja(datetime.fromisoformat("2026-01-05T09:05")) == (
        "1月5日(月) 09:05"
    )
    assert datetime_ja(None) == ""
