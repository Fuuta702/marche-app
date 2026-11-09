from datetime import timedelta

import pytest

from marche import create_app
from marche.extensions import db
from marche.models import (
    Order,
    OrderItem,
    OrderStatus,
    Product,
    Role,
    User,
    now_jst,
)

# 本物の外部サービス（Stripe、拡張で足す Claude API）は、テストでは呼ばない。
# フェイク（偽物）のクラスを create_app() の設定で渡して差し替える。


class FakePayment:
    """marche/services/payment.py の代わり。Stripe を呼ばずに、呼ばれた内容を記録する"""

    def __init__(self):
        self.created = []  # create_checkout_session に渡された注文の ID
        self.sessions = {}  # retrieve_session が返すもの（テストで入れておく）
        self.expired = []  # expire_session に渡された Session の ID
        self.error = None  # 例外を入れておくと、どの関数もそれを投げる

    def _raise_if_error(self):
        if self.error is not None:
            raise self.error

    def create_checkout_session(self, order):
        self._raise_if_error()
        self.created.append(order.id)
        session_id = f"cs_test_fake_{order.id}"
        return session_id, f"https://checkout.stripe.test/c/pay/{session_id}"

    def retrieve_session(self, session_id):
        self._raise_if_error()
        return self.sessions[session_id]

    def expire_session(self, session_id):
        self._raise_if_error()
        self.expired.append(session_id)


@pytest.fixture
def fake_payment():
    return FakePayment()


@pytest.fixture
def app(tmp_path, fake_payment):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
            # テストの写真は一時フォルダーへ。marche/static/uploads に溜めない
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "PAYMENT_SERVICE": fake_payment,
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(role, email, name):
    user = User(name=name, email=email, role=role)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def customer(app):
    return _make_user(Role.CUSTOMER, "customer@example.com", "購入者")


@pytest.fixture
def producer(app):
    return _make_user(Role.PRODUCER, "producer@example.com", "高原ファーム")


@pytest.fixture
def other_producer(app):
    return _make_user(Role.PRODUCER, "other@example.com", "別の農園")


@pytest.fixture
def admin(app):
    return _make_user(Role.ADMIN, "admin@example.com", "管理者")


@pytest.fixture
def make_product(app):
    def do_make(producer, **fields):
        values = {"name": "りんご", "price": 300, "stock": 10, "is_published": True}
        values.update(fields)
        product = Product(producer_id=producer.id, **values)
        db.session.add(product)
        db.session.commit()
        return product

    return do_make


@pytest.fixture
def make_order(app):
    """make_order(user, [(product, 数量), ...], status=..., minutes_ago=...)"""

    def do_make(user, items, status=OrderStatus.PENDING, minutes_ago=0, **fields):
        order = Order(
            user_id=user.id,
            status=status,
            total_amount=sum(product.price * quantity for product, quantity in items),
            created_at=now_jst() - timedelta(minutes=minutes_ago),
            **fields,
        )
        for product, quantity in items:
            order.items.append(
                OrderItem(
                    product_id=product.id,
                    product_name=product.name,
                    unit_price=product.price,
                    quantity=quantity,
                )
            )
        db.session.add(order)
        db.session.commit()
        return order

    return do_make


@pytest.fixture
def login(client):
    def do_login(user):
        return client.post("/login", data={"email": user.email, "password": "password"})

    return do_login
