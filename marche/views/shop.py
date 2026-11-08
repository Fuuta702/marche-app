from flask import Blueprint, abort, render_template
from flask_login import current_user
from sqlalchemy.orm import selectinload

from marche.extensions import db
from marche.models import Product
from marche.services.inventory import MAX_QUANTITY, available_stock

bp = Blueprint("shop", __name__)


def can_view(product: Product) -> bool:
    """公開中なら誰でも。非公開なら出品した生産者と管理者だけ"""
    if product.is_published:
        return True
    if not current_user.is_authenticated:
        return False
    return product.producer_id == current_user.id or current_user.is_admin


@bp.route("/")
def index():
    products = db.session.scalars(
        db.select(Product)
        .filter_by(is_published=True)
        .options(selectinload(Product.producer))
        .order_by(Product.created_at.desc(), Product.id.desc())
    ).all()
    items = [(product, available_stock(product)) for product in products]
    return render_template("shop/index.html", items=items)


@bp.route("/products/<int:product_id>")
def show(product_id: int):
    product = db.get_or_404(Product, product_id)
    if not can_view(product):
        # 非公開の商品は「ある」ことも教えないため、403 ではなく 404
        abort(404)
    stock = available_stock(product)
    # 数量のセレクトは 1〜min(MAX_QUANTITY, いま買える数)。最終的な確認は create_order が行う
    limit = min(MAX_QUANTITY, stock)
    return render_template("shop/show.html", product=product, stock=stock, limit=limit)
