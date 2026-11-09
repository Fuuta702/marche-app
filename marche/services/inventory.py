from datetime import timedelta

from sqlalchemy import func, select

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, User, now_jst

# 在庫の設計:
# - 支払い待ち（pending）の注文も、作成から PENDING_MINUTES 分間は在庫を押さえる。
#   決済ページで支払っている間に、同じ在庫を他の人に売らないため
# - 35 分を過ぎた支払い待ちは押さえない。決済ページの期限（31 分）が切れていて、もう支払われないため
#   （31 分は、決済の章で services/payment.py の Checkout Session の expires_at に設定する予定）
# - 支払い済み（paid）は、確定したときに product.stock から引くので、ここでは数えない
# - キャンセル（cancelled）は押さえない
PENDING_MINUTES = 35  # 決済ページの期限（31 分）より少し長くする


def reserved_quantity(product: Product) -> int:
    """支払い待ちで、作成から PENDING_MINUTES 分以内の注文が押さえている数量の合計"""
    # 時刻は Python で計算して渡す（SQLite 専用の日時関数を使わない）
    cutoff = now_jst() - timedelta(minutes=PENDING_MINUTES)
    total = db.session.scalar(
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order)
        .where(
            OrderItem.product_id == product.id,
            Order.status == OrderStatus.PENDING,
            Order.created_at >= cutoff,
        )
    )
    return int(total)


def available_stock(product: Product) -> int:
    """いま買える数。支払い済みの分は product.stock から既に引かれている。0 未満にはしない"""
    return max(product.stock - reserved_quantity(product), 0)


MAX_QUANTITY = 10  # 1商品あたり1回に買える数


class OrderError(Exception):
    """注文を作れないときの例外。message は画面にそのまま出せる日本語"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _normalize_cart(cart: dict) -> dict[int, int]:
    """{商品ID: 数量} を {int: int} に直す。セッション（JSON）から来るとキーは文字列"""
    items: dict[int, int] = {}
    for key, quantity in cart.items():
        try:
            product_id = int(key)
        except TypeError, ValueError:
            raise OrderError("カートに不正な商品があります") from None
        # True / False も int の仲間なので、はっきり弾く
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise OrderError("カートの数量が正しくありません")
        items[product_id] = items.get(product_id, 0) + quantity
    return items


def _check_items(items: dict[int, int], products: dict[int, Product]) -> None:
    """全ての商品を確かめる。1つでも駄目なら OrderError"""
    for product_id, quantity in items.items():
        product = products.get(product_id)
        # 存在しない商品と非公開の商品は、同じメッセージにする（非公開の商品が「ある」ことを教えない）
        if product is None or not product.is_published:
            raise OrderError("販売していない商品がカートに入っています")
        if not 1 <= quantity <= MAX_QUANTITY:
            raise OrderError(
                f"「{product.name}」の数量は 1〜{MAX_QUANTITY} にしてください"
            )
        stock = available_stock(product)
        if quantity > stock:
            raise OrderError(
                f"「{product.name}」は在庫が足りません（いま買える数: {stock}）"
            )


def create_order(user: User, cart: dict) -> Order:
    """カートから支払い待ちの注文を作る。

    先に全ての商品を確かめ、1つでも駄目なら OrderError（DB には何も足さない）。
    金額は引数で受け取らず、全て DB の product.price から計算する。
    """
    items = _normalize_cart(cart or {})
    if not items:
        raise OrderError("カートが空です")

    # 同時に注文が来ても在庫以上に売らないよう、商品の行をロックしてから確かめる（SELECT ... FOR UPDATE）。
    # 同じ商品を注文する処理は commit まで順番待ちになる。ID の順にロックしてデッドロックを防ぐ。
    # SQLite では無視されるだけなので、開発とテストでも同じコードで動く
    products = {
        product.id: product
        for product in db.session.scalars(
            select(Product)
            .where(Product.id.in_(items.keys()))
            .order_by(Product.id)
            .with_for_update()
        )
    }

    # ---- 確認（ここでは DB に何も足さない） ----
    try:
        _check_items(items, products)
    except OrderError:
        db.session.rollback()  # ロックをすぐ外す
        raise

    # ---- 作成（確認が全部通ったときだけ。commit は1回） ----
    order = Order(user_id=user.id, status=OrderStatus.PENDING, total_amount=0)
    for product_id, quantity in items.items():
        product = products[product_id]
        # 注文時点の商品名と単価（DB の価格）を明細に残す。小計は OrderItem.subtotal
        order.items.append(
            OrderItem(
                product_id=product.id,
                product_name=product.name,
                unit_price=product.price,
                quantity=quantity,
            )
        )
    order.total_amount = sum(item.subtotal for item in order.items)
    db.session.add(order)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return order
