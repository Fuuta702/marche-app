import pytest

from marche.models import OrderStatus
from marche.services.inventory import (
    PENDING_MINUTES,
    available_stock,
    reserved_quantity,
)


@pytest.fixture
def apple(producer, make_product):
    return make_product(producer, name="りんご", stock=10)


def test_注文が無ければ在庫そのまま(apple):
    assert reserved_quantity(apple) == 0
    assert available_stock(apple) == 10


def test_支払い待ちの注文は在庫を押さえる(customer, apple, make_order):
    make_order(customer, [(apple, 3)])
    assert reserved_quantity(apple) == 3
    assert available_stock(apple) == 7


def test_期限の少し前の支払い待ちは押さえる(customer, apple, make_order):
    make_order(customer, [(apple, 3)], minutes_ago=PENDING_MINUTES - 1)
    assert available_stock(apple) == 7


def test_期限を過ぎた支払い待ちは押さえない(customer, apple, make_order):
    # 35 分ちょうどは、テストの実行中に時計が進むと結果が揺れるので避ける
    make_order(customer, [(apple, 3)], minutes_ago=PENDING_MINUTES + 1)
    assert reserved_quantity(apple) == 0
    assert available_stock(apple) == 10


@pytest.mark.parametrize("status", [OrderStatus.PAID, OrderStatus.CANCELLED])
def test_支払い済みとキャンセルは数えない(customer, apple, make_order, status):
    # 支払い済みは、支払いが確定したときに stock から引く。ここで数えると二重に引く
    make_order(customer, [(apple, 3)], status=status)
    assert reserved_quantity(apple) == 0


def test_他の商品の明細は数えない(customer, producer, apple, make_product, make_order):
    pear = make_product(producer, name="なし", stock=10)
    make_order(customer, [(pear, 5)])
    assert reserved_quantity(apple) == 0
    assert reserved_quantity(pear) == 5


def test_複数の注文の数量を合計する(
    customer, admin, producer, apple, make_product, make_order
):
    pear = make_product(producer, name="なし")
    make_order(customer, [(apple, 2), (pear, 1)])
    make_order(admin, [(apple, 4)])
    make_order(customer, [(apple, 9)], status=OrderStatus.PAID)
    make_order(customer, [(apple, 9)], minutes_ago=PENDING_MINUTES + 10)
    assert reserved_quantity(apple) == 6
    assert available_stock(apple) == 4


def test_押さえた数が在庫を超えても0未満にならない(
    customer, admin, producer, make_product, make_order
):
    product = make_product(producer, stock=5)
    make_order(customer, [(product, 4)])
    make_order(admin, [(product, 4)])
    assert reserved_quantity(product) == 8
    assert available_stock(product) == 0
