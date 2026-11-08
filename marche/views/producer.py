from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from marche.decorators import role_required
from marche.extensions import db
from marche.forms import ProductForm
from marche.models import OrderItem, Product, Role
from marche.uploads import delete_image, detect_media_type, save_image

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


def read_photo(form: ProductForm):
    """選ばれた写真の中身を確かめる。
    選ばれていなければ None、画像なら (データ, 種類)、画像でなければフォームにエラーを足して False
    """
    file = form.photo.data
    if not file or not file.filename:
        return None
    data = file.read()
    media_type = detect_media_type(data)
    if media_type is None:
        form.photo.errors.append(
            "画像ではないファイルです。JPEG / PNG / WebP の画像を選んでください"
        )
        return False
    return data, media_type


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
    if form.validate_on_submit() and (photo := read_photo(form)) is not False:
        product = Product(producer_id=current_user.id)
        fill_product(product, form)
        if photo:
            product.image_filename = save_image(*photo)
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
    if form.validate_on_submit() and (photo := read_photo(form)) is not False:
        fill_product(product, form)
        old_filename = product.image_filename
        if photo:
            product.image_filename = save_image(*photo)
        db.session.commit()
        # 古い写真は、新しい写真を DB に書いてから消す（途中で失敗しても、無いファイルを指さないように）
        if photo:
            delete_image(old_filename)
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
    product = get_own_product_or_403(product_id)
    # 注文のある商品は消せない（売上の記録を残す）。SQLite は外部キーの RESTRICT を守らないのでここで確かめる
    has_orders = db.session.scalar(
        db.select(OrderItem.id).filter_by(product_id=product.id).limit(1)
    )
    if has_orders:
        flash(f"「{product.name}」は注文があるので削除できません。非公開にしてください")
        return redirect(url_for("producer.index"))
    name, filename = product.name, product.image_filename
    db.session.delete(product)
    db.session.commit()
    delete_image(filename)
    flash(f"「{name}」を削除しました")
    return redirect(url_for("producer.index"))
