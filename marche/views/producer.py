from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from marche.decorators import role_required
from marche.extensions import db
from marche.forms import ProductForm
from marche.models import Product, Role

bp = Blueprint("producer", __name__, url_prefix="/producer")


def get_own_product_or_403(product_id: int) -> Product:
    """商品を取得し、ログイン中の生産者のものでなければ 403。無ければ 404"""
    product = db.get_or_404(Product, product_id)
    if product.producer_id != current_user.id:
        abort(403)
    return product


def fill_product(product: Product, form: ProductForm) -> None:
    # producer_id はここで触らない
    product.name = form.name.data.strip()
    product.price = form.price.data
    product.stock = form.stock.data
    product.description = (form.description.data or "").strip()
    product.is_published = form.is_published.data


@bp.route("/products")
@role_required(Role.PRODUCER)
def index():
    products = db.session.scalars(
        db.select(Product)
        .filter_by(producer_id=current_user.id)
        .order_by(Product.created_at.desc(), Product.id.desc())
    ).all()
    return render_template("producer/index.html", products=products)


@bp.route("/products/new", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def new():
    form = ProductForm()
    if form.validate_on_submit():
        product = Product(producer_id=current_user.id)
        fill_product(product, form)
        db.session.add(product)
        db.session.commit()
        flash("商品を登録しました")
        return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template("producer/form.html", form=form, product=None), status


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def edit(product_id: int):
    product = get_own_product_or_403(product_id)
    form = ProductForm(obj=product)
    if form.validate_on_submit():
        fill_product(product, form)
        db.session.commit()
        flash("商品を更新しました")
        return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template("producer/form.html", form=form, product=product), status


@bp.route("/products/<int:product_id>/toggle", methods=["POST"])
@role_required(Role.PRODUCER)
def toggle(product_id: int):
    product = get_own_product_or_403(product_id)
    product.is_published = not product.is_published
    db.session.commit()
    state = "公開" if product.is_published else "非公開に"
    flash(f"「{product.name}」を{state}しました")
    return redirect(url_for("producer.index"))


@bp.route("/products/<int:product_id>/delete", methods=["POST"])
@role_required(Role.PRODUCER)
def delete(product_id: int):
    # 注文のある商品を削除できないようにする制限は、注文の章で足す
    product = get_own_product_or_403(product_id)
    name = product.name
    db.session.delete(product)
    db.session.commit()
    flash(f"「{name}」を削除しました")
    return redirect(url_for("producer.index"))
