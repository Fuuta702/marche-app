from flask import Blueprint, abort, flash, redirect, render_template, session, url_for
from flask_login import current_user, login_required

from marche.extensions import db
from marche.models import Order
from marche.services.inventory import OrderError, create_order
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
    session.pop("cart", None)
    # 決済（Stripe）は平日課題で、ここから決済ページへ移るように変える
    flash("注文を受け付けました（支払い待ち）")
    return redirect(url_for("orders.show", order_id=order.id))


@bp.route("/<int:order_id>")
@login_required
def show(order_id: int):
    order = get_own_order_or_403(order_id)
    return render_template("orders/show.html", order=order)
