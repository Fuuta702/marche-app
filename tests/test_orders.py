import pytest
from sqlalchemy.exc import IntegrityError

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product


@pytest.fixture
def apple(producer, make_product):
    return make_product(producer, name="りんご", price=300)


def test_注文と明細を保存できる(customer, apple, make_order):
    order = make_order(customer, [(apple, 2)])
    assert order.status == OrderStatus.PENDING
    assert order.total_amount == 600
    assert order.user == customer
    assert customer.orders == [order]
    assert order.items[0].product == apple
    assert order.items[0].subtotal == 600
    assert apple.order_items == order.items


@pytest.mark.parametrize(
    "status, label",
    [
        (OrderStatus.PENDING, "支払い待ち"),
        (OrderStatus.PAID, "支払い済み"),
        (OrderStatus.CANCELLED, "キャンセル"),
    ],
)
def test_状態の日本語のラベル(customer, apple, make_order, status, label):
    assert make_order(customer, [(apple, 1)], status=status).status_label == label


def test_注文を消すと明細も消える(customer, apple, make_order):
    order = make_order(customer, [(apple, 1)])
    db.session.delete(order)
    db.session.commit()
    assert db.session.scalar(db.select(OrderItem)) is None
    assert db.session.get(Product, apple.id) is not None  # 商品は残る


def test_明細は注文時点の商品名と単価のまま(customer, apple, make_order):
    order = make_order(customer, [(apple, 2)])
    apple.name = "名前を変えたりんご"
    apple.price = 999
    db.session.commit()
    db.session.refresh(order)
    assert order.items[0].product_name == "りんご"
    assert order.items[0].unit_price == 300
    assert order.total_amount == 600


def test_同じCheckoutSessionのIDは保存できない(customer, apple, make_order):
    make_order(customer, [(apple, 1)], stripe_checkout_session_id="cs_test_1")
    with pytest.raises(IntegrityError):
        make_order(customer, [(apple, 1)], stripe_checkout_session_id="cs_test_1")
    db.session.rollback()


def test_CheckoutSessionのIDが無い注文は複数あってよい(customer, apple, make_order):
    make_order(customer, [(apple, 1)])
    make_order(customer, [(apple, 1)])
    assert len(db.session.scalars(db.select(Order)).all()) == 2


def test_同じ注文に同じ商品を2行入れられない(customer, apple, make_order):
    with pytest.raises(IntegrityError):
        make_order(customer, [(apple, 1), (apple, 2)])
    db.session.rollback()


@pytest.mark.parametrize("quantity", [0, 11])
def test_数量は1から10まで(customer, apple, make_order, quantity):
    with pytest.raises(IntegrityError):
        make_order(customer, [(apple, quantity)])
    db.session.rollback()


# ---- 注文のある商品の削除（producer.delete） ----


def test_注文のある商品は削除できない(
    client, customer, producer, login, apple, make_order
):
    make_order(customer, [(apple, 1)])
    login(producer)
    response = client.post(
        f"/producer/products/{apple.id}/delete", follow_redirects=True
    )
    assert response.status_code == 200
    assert "注文があるので削除できません" in response.text
    assert db.session.get(Product, apple.id) is not None


def test_キャンセルされた注文がある商品も削除できない(
    client, customer, producer, login, apple, make_order
):
    # 売上の記録（明細）を残すため、状態にかかわらず消さない
    make_order(customer, [(apple, 1)], status=OrderStatus.CANCELLED)
    login(producer)
    client.post(f"/producer/products/{apple.id}/delete")
    assert db.session.get(Product, apple.id) is not None
