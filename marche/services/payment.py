"""Stripe を呼ぶのはこのファイルだけ（views からは app.extensions["payment"] 経由で呼ぶ）"""

import logging
import time

import stripe
from flask import current_app, url_for

from marche.models import Order

logger = logging.getLogger(__name__)

# 決済ページ（Checkout Session）の期限。Stripe の最短は 30 分。
# 在庫を押さえる inventory.PENDING_MINUTES（35 分）より短くする
CHECKOUT_EXPIRES_MINUTES = 31


class PaymentError(Exception):
    """決済の処理に失敗したときの例外。message は画面にそのまま出せる日本語"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _client() -> stripe.StripeClient:
    key = current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        raise PaymentError("決済の設定がされていません。管理者に連絡してください")
    return stripe.StripeClient(key)


def create_checkout_session(order: Order) -> tuple[str, str]:
    """注文の決済ページを作り、(Session の ID, 決済ページの URL) を返す。金額は注文明細（DB）から取る"""
    # {CHECKOUT_SESSION_ID} は Stripe が置き換える。url_for に渡すと { } が変換されるので、後から付ける
    success_url = (
        url_for("orders.complete", order_id=order.id, _external=True)
        + "?session_id={CHECKOUT_SESSION_ID}"
    )
    params = {
        "mode": "payment",
        "line_items": [
            {
                "quantity": item.quantity,
                "price_data": {
                    "currency": "jpy",
                    # JPY は小数の無い通貨なので、円のまま（100 倍しない）
                    "unit_amount": item.unit_price,
                    "product_data": {"name": item.product_name},
                },
            }
            for item in order.items
        ],
        "customer_email": order.user.email,
        "metadata": {"order_id": str(order.id)},
        "success_url": success_url,
        "cancel_url": url_for("orders.cancel", order_id=order.id, _external=True),
        # Stripe は UTC の秒（Unix 時間）で受け取る
        "expires_at": int(time.time()) + CHECKOUT_EXPIRES_MINUTES * 60,
    }
    try:
        session = _client().v1.checkout.sessions.create(params)
    except stripe.StripeError as error:
        # 詳細はログへ（キーは含まれない）。画面には短い日本語を出す
        logger.warning(
            "Checkout Session を作れませんでした（注文 %s）: %s", order.id, error
        )
        raise PaymentError(
            "決済ページを開けませんでした。時間をおいてもう一度お試しください"
        ) from error
    return session.id, session.url


def retrieve_session(session_id: str):
    """Checkout Session を取得する（完了ページで支払いを確かめるため）"""
    try:
        return _client().v1.checkout.sessions.retrieve(session_id)
    except stripe.StripeError as error:
        logger.warning(
            "Checkout Session を取得できませんでした（%s）: %s", session_id, error
        )
        raise PaymentError(
            "お支払いの状況を確認できませんでした。時間をおいてもう一度お試しください"
        ) from error


def expire_session(session_id: str):
    """Checkout Session を期限切れにする（中断ページで注文を取り消す前に、支払えないようにするため）"""
    try:
        return _client().v1.checkout.sessions.expire(session_id)
    except stripe.StripeError as error:
        logger.warning(
            "Checkout Session を期限切れにできませんでした（%s）: %s", session_id, error
        )
        raise PaymentError(
            "決済ページを閉じられませんでした。お支払いが済んでいる可能性があるため、注文は取り消していません"
        ) from error
