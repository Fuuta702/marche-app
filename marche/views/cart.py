from flask import Blueprint, abort, flash, redirect, render_template, session, url_for

from marche.extensions import db
from marche.forms import CartQuantityForm
from marche.models import Product
from marche.services.inventory import MAX_QUANTITY, available_stock

bp = Blueprint("cart", __name__, url_prefix="/cart")


def get_cart() -> dict[int, int]:
    """セッションのカートを {商品ID: 数量} で返す。JSON なのでキーは文字列で入っている"""
    cart = {}
    for key, quantity in session.get("cart", {}).items():
        # 改ざんなどで数字でないものが入っていたら捨てる
        try:
            product_id = int(key)
        except TypeError, ValueError:
            continue
        if isinstance(quantity, int) and not isinstance(quantity, bool):
            cart[product_id] = quantity
    return cart


def save_cart(cart: dict[int, int]) -> None:
    session["cart"] = {
        str(product_id): quantity for product_id, quantity in cart.items()
    }


def max_quantity(product: Product) -> int:
    """1回にカートに入れられる数の上限"""
    return min(MAX_QUANTITY, available_stock(product))


def get_published_product_or_404(product_id: int) -> Product:
    product = db.session.get(Product, product_id)
    if product is None or not product.is_published:
        abort(404)
    return product


@bp.route("/")
def show():
    cart = get_cart()
    products = {
        product.id: product
        for product in db.session.scalars(
            db.select(Product).where(Product.id.in_(cart.keys()))
        )
    }
    lines = []
    for product_id, quantity in cart.items():
        product = products.get(product_id)
        if product is None or not product.is_published:
            continue
        # 小計と合計は表示のためだけ。注文するときは create_order が DB の価格で計算し直す
        lines.append((product, quantity, product.price * quantity))
    # 非公開になった商品や消えた商品は、セッションからも外す（注文時に OrderError にならないように）
    visible = {product.id: quantity for product, quantity, _ in lines}
    if visible != cart:
        save_cart(visible)
    total = sum(subtotal for _, _, subtotal in lines)
    return render_template("cart/show.html", lines=lines, total=total)


@bp.route("/add/<int:product_id>", methods=["POST"])
def add(product_id: int):
    product = get_published_product_or_404(product_id)
    form = CartQuantityForm()
    back = url_for("shop.show", product_id=product.id)
    if not form.validate_on_submit():
        flash("数量を正しく選んでください")
        return redirect(back)
    cart = get_cart()
    new_quantity = cart.get(product.id, 0) + form.quantity.data
    limit = max_quantity(product)
    if limit < 1:
        flash(f"「{product.name}」は売り切れです")
        return redirect(back)
    if not 1 <= new_quantity <= limit:
        flash(
            f"「{product.name}」は 1〜{limit} 個にしてください"
            f"（カートに {cart.get(product.id, 0)} 個あります）"
        )
        return redirect(back)
    cart[product.id] = new_quantity
    save_cart(cart)
    flash(f"「{product.name}」をカートに入れました")
    return redirect(url_for("cart.show"))


@bp.route("/update/<int:product_id>", methods=["POST"])
def update(product_id: int):
    form = CartQuantityForm()
    if not form.validate_on_submit():
        flash("数量を正しく入力してください")
        return redirect(url_for("cart.show"))
    cart = get_cart()
    quantity = form.quantity.data
    if quantity <= 0:
        # 外すだけなら、非公開になった商品でもよい
        cart.pop(product_id, None)
        save_cart(cart)
        flash("カートから外しました")
        return redirect(url_for("cart.show"))
    product = get_published_product_or_404(product_id)
    limit = max_quantity(product)
    if quantity > limit:
        flash(f"「{product.name}」は {limit} 個までです")
        return redirect(url_for("cart.show"))
    cart[product.id] = quantity
    save_cart(cart)
    flash("数量を変えました")
    return redirect(url_for("cart.show"))
