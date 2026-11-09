from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from marche.extensions import db
from marche.models import Order, OrderStatus
from marche.services import inventory
from marche.services.inventory import OrderError, create_order
from marche.services.payment import PaymentError
from marche.views.cart import get_cart

bp = Blueprint("orders", __name__, url_prefix="/orders")


def get_own_order_or_403(order_id: int) -> Order:
    """注文を取得し、本人でも管理者でもなければ 403。無ければ 404"""
    order = db.get_or_404(Order, order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return order


@bp.route("/", methods=["POST"])
@login_required
def create():
    try:
        # 在庫・数量・公開状態の確認と金額の計算は、全て create_order（サービス）の中で行う
        order = create_order(current_user, get_cart())
    except OrderError as error:
        flash(error.message)
        return redirect(url_for("cart.show"))
    payment = current_app.extensions["payment"]
    try:
        session_id, checkout_url = payment.create_checkout_session(order)
    except PaymentError as error:
        # 決済ページを作れなかったら、作った注文を取り消す（押さえた在庫が戻る）。カートは残す
        inventory.cancel(order)
        flash(error.message)
        return redirect(url_for("cart.show"))
    order.stripe_checkout_session_id = session_id
    db.session.commit()
    session.pop("cart", None)
    # 303: POST のあとに GET で決済ページを開かせる
    return redirect(checkout_url, code=303)


@bp.route("/<int:order_id>")
@login_required
def show(order_id: int):
    order = get_own_order_or_403(order_id)
    return render_template("orders/show.html", order=order)


@bp.route("/<int:order_id>/cancel", methods=["GET", "POST"])
@login_required
def cancel(order_id: int):
    """支払い中断ページ。Stripe の決済ページで「戻る」を押すとここに来る"""
    order = get_own_order_or_403(order_id)
    if request.method == "GET":
        return render_template("orders/cancel.html", order=order)

    if order.status != OrderStatus.PENDING:
        flash("この注文は取り消せません")
        return redirect(url_for("orders.show", order_id=order.id))
    if order.stripe_checkout_session_id:
        try:
            # 先に決済ページを期限切れにして、取り消したあとに支払われないようにする
            current_app.extensions["payment"].expire_session(
                order.stripe_checkout_session_id
            )
        except PaymentError as error:
            # 期限切れにできない＝支払いが済んでいるかもしれない。取り消さない
            flash(error.message)
            return redirect(url_for("orders.show", order_id=order.id))
    if inventory.cancel(order):
        flash("注文を取り消しました")
    else:
        flash("この注文は取り消せません")
    return redirect(url_for("orders.show", order_id=order.id))


@bp.route("/<int:order_id>/complete")
@login_required
def complete(order_id: int):
    """支払い完了ページ（仮）。支払いの確定は 08 章で作る"""
    order = get_own_order_or_403(order_id)
    return render_template("orders/complete.html", order=order)
