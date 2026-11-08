from datetime import timedelta

from sqlalchemy import func, select

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, now_jst

# 支払い待ちの注文が在庫を押さえる時間（分）。
# Stripe の Checkout Session の有効期限（最短 30 分）より少し長くする
PENDING_MINUTES = 35


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
