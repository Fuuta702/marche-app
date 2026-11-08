from datetime import timedelta

from sqlalchemy import func, select

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, now_jst

# 在庫の設計:
# - 支払い待ち（pending）の注文も、作成から PENDING_MINUTES 分間は在庫を押さえる。
#   決済ページで支払っている間に、同じ在庫を他の人に売らないため
# - 35 分を過ぎた支払い待ちは押さえない。決済ページの期限（31 分）が切れていて、もう支払われないため
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
