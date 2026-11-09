import pytest

from marche.extensions import db
from marche.models import Order, OrderStatus, Role, User
from marche.services import inventory
from marche.services.inventory import available_stock
from marche.services.payment import PaymentError


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


def test_注文すると決済ページへ303で移りカートが空になる(
    client, customer, login, apple, fake_payment
):
    login(customer)
    set_cart(client, {str(apple.id): 2})
    response = client.post("/orders/")
    order = orders()[0]
    assert response.status_code == 303
    assert (
        response.headers["Location"]
        == f"https://checkout.stripe.test/c/pay/cs_test_fake_{order.id}"
    )
    assert fake_payment.created == [order.id]
    assert order.stripe_checkout_session_id == f"cs_test_fake_{order.id}"
    assert order.user_id == customer.id
    assert order.status == OrderStatus.PENDING
    assert order.total_amount == 2400
    assert cart_of(client) == {}


# ---- 決済ページを作れなかったとき ----


def test_決済ページを作れなければ注文を取り消してカートに戻る(
    client, customer, login, apple, fake_payment
):
    fake_payment.error = PaymentError("決済ページを開けませんでした")
    login(customer)
    set_cart(client, {str(apple.id): 2})
    response = client.post("/orders/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    order = orders()[0]
    assert order.status == OrderStatus.CANCELLED
    assert order.stripe_checkout_session_id is None
    assert cart_of(client) == {str(apple.id): 2}  # カートは残す
    assert available_stock(apple) == 5  # 押さえた在庫は戻る
    assert "決済ページを開けませんでした" in client.get("/cart/").text


def test_在庫不足のときは決済ページを作らない(
    client, customer, login, apple, fake_payment
):
    login(customer)
    set_cart(client, {str(apple.id): 6})
    client.post("/orders/")
    assert fake_payment.created == []
    assert orders() == []


def test_決済ページを作れなかったあともう一度注文すると決済ページへ移れる(
    client, customer, login, apple, fake_payment
):
    fake_payment.error = PaymentError("決済ページを開けませんでした")
    login(customer)
    set_cart(client, {str(apple.id): 2})
    client.post("/orders/")
    fake_payment.error = None
    response = client.post("/orders/")
    first, second = sorted(orders(), key=lambda order: order.id)
    assert first.status == OrderStatus.CANCELLED
    assert second.status == OrderStatus.PENDING
    assert response.status_code == 303
    assert response.headers["Location"].endswith(f"cs_test_fake_{second.id}")
    assert cart_of(client) == {}
    assert available_stock(apple) == 3  # 押さえているのは2つ目の注文だけ


def test_決済ページを作れなかった注文の在庫は他の人が買える(
    client, customer, producer, login, make_product, fake_payment
):
    scarce = make_product(producer, name="限定", price=500, stock=1)
    fake_payment.error = PaymentError("決済ページを開けませんでした")
    login(customer)
    set_cart(client, {str(scarce.id): 1})
    client.post("/orders/")
    assert available_stock(scarce) == 1
    fake_payment.error = None
    other = client.application.test_client()
    other_user = User(name="別の購入者", email="b@example.com", role=Role.CUSTOMER)
    other_user.set_password("password")
    db.session.add(other_user)
    db.session.commit()
    other.post("/login", data={"email": "b@example.com", "password": "password"})
    set_cart(other, {str(scarce.id): 1})
    assert other.post("/orders/").status_code == 303


def test_二つ注文するとSessionのIDは別になる(
    client, customer, login, apple, fake_payment
):
    login(customer)
    set_cart(client, {str(apple.id): 1})
    client.post("/orders/")
    set_cart(client, {str(apple.id): 1})
    client.post("/orders/")
    ids = [order.stripe_checkout_session_id for order in orders()]
    assert len(ids) == 2
    assert len(set(ids)) == 2


# ---- 支払いの中断 ----


@pytest.fixture
def pending_order(customer, apple, make_order):
    return make_order(
        customer, [(apple, 2)], stripe_checkout_session_id="cs_test_pending"
    )


def test_中断ページに取り消しボタンが出る(client, customer, login, pending_order):
    login(customer)
    response = client.get(f"/orders/{pending_order.id}/cancel")
    assert response.status_code == 200
    assert "この注文を取り消す" in response.text
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.PENDING  # GET では何も変えない


def test_中断ページで取り消すと期限切れにしてから取り消す(
    client, customer, login, apple, pending_order, fake_payment
):
    login(customer)
    response = client.post(f"/orders/{pending_order.id}/cancel")
    assert response.headers["Location"] == f"/orders/{pending_order.id}"
    assert fake_payment.expired == ["cs_test_pending"]
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.CANCELLED
    assert available_stock(apple) == 5


def test_期限切れにできなければ取り消さない(
    client, customer, login, pending_order, fake_payment
):
    fake_payment.error = PaymentError("決済ページを閉じられませんでした")
    login(customer)
    response = client.post(f"/orders/{pending_order.id}/cancel", follow_redirects=True)
    assert "決済ページを閉じられませんでした" in response.text
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.PENDING


def test_支払い済みの注文は中断ページから取り消せない(
    client, customer, login, apple, make_order, fake_payment
):
    order = make_order(
        customer,
        [(apple, 1)],
        status=OrderStatus.PAID,
        stripe_checkout_session_id="cs_test_paid",
    )
    login(customer)
    assert "この注文を取り消す" not in client.get(f"/orders/{order.id}/cancel").text
    client.post(f"/orders/{order.id}/cancel")
    db.session.refresh(order)
    assert order.status == OrderStatus.PAID
    assert fake_payment.expired == []


def test_取り消したあともう一度取り消してもexpireは1回だけ(
    client, customer, login, pending_order, fake_payment
):
    login(customer)
    client.post(f"/orders/{pending_order.id}/cancel")
    response = client.post(f"/orders/{pending_order.id}/cancel", follow_redirects=True)
    assert "この注文は取り消せません" in response.text
    assert fake_payment.expired == ["cs_test_pending"]
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.CANCELLED


def test_取り消し済みの注文を中断ページで取り消そうとしても何も起きない(
    client, customer, login, apple, make_order, fake_payment
):
    order = make_order(
        customer,
        [(apple, 1)],
        status=OrderStatus.CANCELLED,
        stripe_checkout_session_id="cs_test_done",
    )
    login(customer)
    response = client.post(f"/orders/{order.id}/cancel", follow_redirects=True)
    assert "この注文は取り消せません" in response.text
    assert fake_payment.expired == []
    db.session.refresh(order)
    assert order.status == OrderStatus.CANCELLED


def test_Session_IDの無い注文はexpireを呼ばずに取り消す(
    client, customer, login, apple, make_order, fake_payment
):
    order = make_order(customer, [(apple, 2)])
    login(customer)
    client.post(f"/orders/{order.id}/cancel")
    assert fake_payment.expired == []
    db.session.refresh(order)
    assert order.status == OrderStatus.CANCELLED
    assert available_stock(apple) == 5


def test_管理者は他人の注文を中断ページで取り消せる(
    client, admin, login, pending_order, fake_payment
):
    # 今の実装: get_own_order_or_403 は管理者を通すので、取り消せる
    login(admin)
    client.post(f"/orders/{pending_order.id}/cancel")
    assert fake_payment.expired == ["cs_test_pending"]
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.CANCELLED


def test_存在しない注文の中断ページは404(client, customer, login):
    login(customer)
    assert client.get("/orders/9999/cancel").status_code == 404
    assert client.post("/orders/9999/cancel").status_code == 404


@pytest.mark.parametrize("page", ["cancel", "complete"])
def test_中断と完了のページは他人には403(
    client, producer, other_producer, login, pending_order, page
):
    login(other_producer)
    assert client.get(f"/orders/{pending_order.id}/{page}").status_code == 403


def test_他人は中断ページから取り消せない(
    client, other_producer, login, pending_order, fake_payment
):
    login(other_producer)
    assert client.post(f"/orders/{pending_order.id}/cancel").status_code == 403
    db.session.refresh(pending_order)
    assert pending_order.status == OrderStatus.PENDING
    assert fake_payment.expired == []


@pytest.mark.parametrize("page", ["cancel", "complete"])
def test_中断と完了のページは未ログインならログイン画面へ(client, pending_order, page):
    response = client.get(f"/orders/{pending_order.id}/{page}")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


def test_完了ページは仮の表示(client, customer, login, pending_order):
    login(customer)
    response = client.get(
        f"/orders/{pending_order.id}/complete?session_id=cs_test_pending"
    )
    assert response.status_code == 200
    assert "お支払いを確認しています" in response.text


# ---- inventory.cancel ----


@pytest.mark.parametrize(
    "status, expected",
    [
        (OrderStatus.PENDING, True),
        (OrderStatus.PAID, False),
        (OrderStatus.CANCELLED, False),
    ],
)
def test_取り消せるのは支払い待ちだけ(customer, apple, make_order, status, expected):
    order = make_order(customer, [(apple, 1)], status=status)
    assert inventory.cancel(order) is expected
    expected_status = OrderStatus.CANCELLED if expected else status
    assert order.status == expected_status


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
