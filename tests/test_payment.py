"""marche/services/payment.py のテスト。_client を差し替えるので、本物の Stripe は呼ばない"""

import time
from types import SimpleNamespace

import pytest
import stripe

from marche.services import payment
from marche.services.inventory import PENDING_MINUTES
from marche.services.payment import PaymentError


class FakeSessions:
    """client.v1.checkout.sessions の代わり。渡された引数を記録する"""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def _call(self, name, *args):
        self.calls.append((name, args))
        if self.error:
            raise self.error
        return SimpleNamespace(
            id="cs_test_123", url="https://checkout.stripe.com/c/pay/cs_test_123"
        )

    def create(self, params):
        return self._call("create", params)

    def retrieve(self, session_id):
        return self._call("retrieve", session_id)

    def expire(self, session_id):
        return self._call("expire", session_id)


@pytest.fixture
def sessions(monkeypatch):
    fake = FakeSessions()
    client = SimpleNamespace(
        v1=SimpleNamespace(checkout=SimpleNamespace(sessions=fake))
    )
    monkeypatch.setattr(payment, "_client", lambda: client)
    return fake


@pytest.fixture
def order(customer, producer, make_product, make_order):
    apple = make_product(producer, name="りんご", price=300)
    miso = make_product(producer, name="味噌", price=1300)
    return make_order(customer, [(apple, 2), (miso, 1)])


def test_決済ページの作成に渡す内容(app, sessions, order):
    with app.test_request_context():
        before = int(time.time())
        session_id, url = payment.create_checkout_session(order)
    assert (session_id, url) == (
        "cs_test_123",
        "https://checkout.stripe.com/c/pay/cs_test_123",
    )

    name, (params,) = sessions.calls[0]
    assert name == "create"
    assert params["mode"] == "payment"
    assert params["customer_email"] == order.user.email
    assert params["metadata"] == {"order_id": str(order.id)}

    lines = {
        line["price_data"]["product_data"]["name"]: line
        for line in params["line_items"]
    }
    assert set(lines) == {"りんご", "味噌"}
    # 金額は注文明細（DB）の単価。JPY なので 100 倍しない
    assert lines["りんご"]["price_data"]["unit_amount"] == 300
    assert lines["りんご"]["price_data"]["currency"] == "jpy"
    assert lines["りんご"]["quantity"] == 2
    assert lines["味噌"]["price_data"]["unit_amount"] == 1300
    assert lines["味噌"]["quantity"] == 1

    # {CHECKOUT_SESSION_ID} は Stripe が置き換えるので、そのまま渡す（%7B などに変えない）
    assert params["success_url"] == (
        f"http://localhost/orders/{order.id}/complete?session_id={{CHECKOUT_SESSION_ID}}"
    )
    assert params["cancel_url"] == f"http://localhost/orders/{order.id}/cancel"

    # 31 分後（UTC の秒）
    expected = before + payment.CHECKOUT_EXPIRES_MINUTES * 60
    assert expected <= params["expires_at"] <= expected + 5


def test_金額は今の商品の価格ではなく明細の単価(app, sessions, order):
    order.items[0].product.price = 99999  # 注文のあとで価格が変わっても
    with app.test_request_context():
        payment.create_checkout_session(order)
    _, (params,) = sessions.calls[0]
    amounts = sorted(line["price_data"]["unit_amount"] for line in params["line_items"])
    assert amounts == [300, 1300]


def test_line_itemsの数は明細の数と同じ(app, sessions, order):
    with app.test_request_context():
        payment.create_checkout_session(order)
    _, (params,) = sessions.calls[0]
    assert len(params["line_items"]) == len(order.items) == 2


def test_Stripeのエラーは日本語のPaymentErrorになる(app, sessions, order):
    sessions.error = stripe.StripeError("No such price")
    with app.test_request_context(), pytest.raises(PaymentError) as error:
        payment.create_checkout_session(order)
    assert "決済ページを開けませんでした" in error.value.message
    assert "No such price" not in error.value.message  # 英語の詳細は画面に出さない


@pytest.mark.parametrize("func", ["retrieve_session", "expire_session"])
def test_取得と期限切れはSessionのIDを渡す(app, sessions, func):
    getattr(payment, func)("cs_test_abc")
    assert sessions.calls == [(func.split("_")[0], ("cs_test_abc",))]


@pytest.mark.parametrize("func", ["retrieve_session", "expire_session"])
def test_取得と期限切れのエラーもPaymentErrorになる(app, sessions, func):
    sessions.error = stripe.StripeError("boom")
    with pytest.raises(PaymentError):
        getattr(payment, func)("cs_test_abc")


def test_キーが無ければPaymentError(app):
    app.config["STRIPE_SECRET_KEY"] = ""
    with pytest.raises(PaymentError) as error:
        payment._client()
    assert "決済の設定がされていません" in error.value.message


def test_キーがあればStripeClientを作る(app):
    app.config["STRIPE_SECRET_KEY"] = "sk_test_dummy"
    assert isinstance(payment._client(), stripe.StripeClient)  # 作るだけで通信はしない


def test_在庫を押さえる時間は決済ページの期限より長い():
    assert PENDING_MINUTES > payment.CHECKOUT_EXPIRES_MINUTES


def test_テストではフェイクに差し替わっている(app, fake_payment):
    assert app.extensions["payment"] is fake_payment


def test_設定が無ければ本物のpaymentモジュールを使う(tmp_path):
    from marche import create_app

    real_app = create_app(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    assert real_app.extensions["payment"] is payment
