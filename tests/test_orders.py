import pytest
from sqlalchemy.exc import IntegrityError

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product
from marche.services.inventory import MAX_QUANTITY, OrderError, create_order


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


# ---- create_order（カートから注文を作る） ----


def order_count():
    return len(db.session.scalars(db.select(Order)).all()), len(
        db.session.scalars(db.select(OrderItem)).all()
    )


def test_合計はDBの価格と数量から計算する(customer, producer, make_product):
    apple = make_product(producer, name="りんご", price=300, stock=10)
    miso = make_product(producer, name="味噌", price=1300, stock=10)
    order = create_order(customer, {apple.id: 2, miso.id: 1})
    assert order.total_amount == 300 * 2 + 1300 * 1
    assert order.status == OrderStatus.PENDING
    assert order.user_id == customer.id
    items = {item.product_id: item for item in order.items}
    assert items[apple.id].product_name == "りんご"
    assert items[apple.id].unit_price == 300
    assert items[apple.id].quantity == 2
    assert items[apple.id].subtotal == 600
    assert order_count() == (1, 2)


def test_セッションのように文字列のキーでも注文できる(customer, apple):
    order = create_order(customer, {str(apple.id): 3})
    assert order.items[0].quantity == 3


def test_カートに金額を混ぜても使わない(customer, apple):
    # 金額のキーは商品IDとして読めないので弾かれる。金額を引数で渡す方法は無い
    with pytest.raises(OrderError):
        create_order(customer, {apple.id: 1, "price": 1})
    assert order_count() == (0, 0)


@pytest.mark.parametrize("quantity", [1, MAX_QUANTITY])
def test_数量の境目で注文できる(customer, producer, make_product, quantity):
    product = make_product(producer, stock=MAX_QUANTITY)
    order = create_order(customer, {product.id: quantity})
    assert order.items[0].quantity == quantity


def test_在庫ちょうどまで注文できる(customer, producer, make_product):
    product = make_product(producer, stock=3)
    assert create_order(customer, {product.id: 3}).items[0].quantity == 3


def test_在庫を超えると注文は作られない(customer, producer, make_product):
    product = make_product(producer, name="ピクルス", stock=3)
    with pytest.raises(OrderError) as error:
        create_order(customer, {product.id: 4})
    assert "在庫が足りません" in error.value.message
    assert "いま買える数: 3" in error.value.message
    assert order_count() == (0, 0)


def test_支払い待ちで押さえられた分も在庫に数える(
    customer, admin, producer, make_product, make_order
):
    product = make_product(producer, stock=5)
    make_order(admin, [(product, 4)])  # 他の人の支払い待ち
    with pytest.raises(OrderError):
        create_order(customer, {product.id: 2})
    assert create_order(customer, {product.id: 1}).items[0].quantity == 1


def test_非公開の商品は注文できない(customer, producer, make_product):
    product = make_product(producer, is_published=False)
    with pytest.raises(OrderError) as error:
        create_order(customer, {product.id: 1})
    assert "販売していない商品" in error.value.message
    assert order_count() == (0, 0)


@pytest.mark.parametrize(
    "cart",
    [
        {},
        None,
        {9999: 1},  # 存在しない商品
        {"abc": 1},  # 商品IDが数字でない
    ],
)
def test_不正なカートでは注文できない(customer, cart):
    with pytest.raises(OrderError):
        create_order(customer, cart)
    assert order_count() == (0, 0)


@pytest.mark.parametrize("quantity", [0, -1, MAX_QUANTITY + 1, "2", 1.5, True])
def test_不正な数量では注文できない(customer, producer, make_product, quantity):
    product = make_product(producer, stock=100)
    with pytest.raises(OrderError):
        create_order(customer, {product.id: quantity})
    assert order_count() == (0, 0)


def test_1つでも駄目なら他の商品の注文も作らない(customer, producer, make_product):
    ok = make_product(producer, name="在庫あり", stock=10)
    ng = make_product(producer, name="在庫なし", stock=0)
    with pytest.raises(OrderError):
        create_order(customer, {ok.id: 1, ng.id: 1})
    assert order_count() == (0, 0)
