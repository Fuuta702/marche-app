import click
from sqlalchemy import delete

from marche.extensions import db
from marche.models import Order, OrderItem, Product, Role, User
from marche.uploads import delete_image

# 仕様書 4 の「初期データ」。パスワードは全員 password
SEED_USERS = [
    (Role.ADMIN, "admin@example.com", "管理者"),
    (Role.PRODUCER, "producer1@example.com", "高原ファーム"),
    (Role.PRODUCER, "producer2@example.com", "岩手山麓工房"),
    (Role.CUSTOMER, "customer1@example.com", "購入者1"),
    (Role.CUSTOMER, "customer2@example.com", "購入者2"),
]

# (生産者のメール, 商品名, 価格, 在庫, 公開するか, 紹介文)
SEED_PRODUCTS = [
    (
        "producer1@example.com",
        "八幡平マッシュルーム 200g",
        450,
        20,
        True,
        "八幡平の涼しい気候で育てた、肉厚のマッシュルームです。\nソテーやスープにどうぞ。",
    ),
    (
        "producer1@example.com",
        "安代りんどう 切り花 5本",
        800,
        10,
        True,
        "安代地区で育てた、深い青のりんどうです。",
    ),
    (
        "producer1@example.com",
        "ほうれん草 1束",
        180,
        30,
        True,
        "朝に収穫したほうれん草です。",
    ),
    (
        "producer1@example.com",
        "試作中の干し野菜セット",
        600,
        5,
        False,
        "数種類の野菜を天日で干したセットです（試作中）。",
    ),
    (
        "producer2@example.com",
        "八幡平ポーク ロース 300g",
        1200,
        8,
        True,
        "八幡平で育てた豚のロース肉です。",
    ),
    (
        "producer2@example.com",
        "手づくり味噌 1kg",
        1300,
        12,
        True,
        "地元の大豆と米麹で仕込んだ味噌です。",
    ),
    (
        "producer2@example.com",
        "りんごジュース 1L",
        700,
        15,
        True,
        "岩手のりんごをしぼったストレートジュースです。",
    ),
    (
        "producer2@example.com",
        "地熱野菜のピクルス",
        550,
        0,
        True,
        "地熱ハウスで育てた野菜のピクルスです。",
    ),
]


def run_seed() -> None:
    # 写真のファイル名は、DB を消す前に集めておく（消したあとでは分からない）
    filenames = list(
        db.session.scalars(
            db.select(Product.image_filename).where(Product.image_filename.is_not(None))
        )
    )
    # 外部キーの順に消す（明細 → 注文 → 商品 → ユーザー）
    for model in (OrderItem, Order, Product, User):
        db.session.execute(delete(model))
    db.session.commit()
    # ファイルの削除は取り消せないので、DB の削除が確定してから行う
    for filename in filenames:
        delete_image(filename)
    # まとめて消してもセッションは前のオブジェクトを覚えている。同じ ID と混ざらないように忘れさせる
    db.session.expunge_all()

    users = {}
    for role, email, name in SEED_USERS:
        user = User(name=name, email=email, role=role)
        user.set_password("password")
        db.session.add(user)
        users[email] = user
    db.session.flush()

    for email, name, price, stock, published, description in SEED_PRODUCTS:
        db.session.add(
            Product(
                producer_id=users[email].id,
                name=name,
                price=price,
                stock=stock,
                is_published=published,
                description=description,
            )
        )
    db.session.commit()


def init_app(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt="メールアドレス")
    @click.option("--name", prompt="名前", default="管理者")
    @click.password_option("--password", prompt="パスワード")
    def create_admin(email, name, password):
        """管理者を1人作る（画面からは作れないため）"""
        email = email.strip().lower()
        if db.session.scalar(db.select(User).filter_by(email=email)):
            raise click.ClickException("このメールアドレスは登録済みです")
        if len(password) < 8:
            raise click.ClickException("パスワードは8文字以上にしてください")
        user = User(name=name, email=email, role=Role.ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"管理者 {email} を作りました")

    @app.cli.command("seed")
    @click.option("--yes", is_flag=True, help="確認を出さずに実行する")
    def seed(yes):
        """開発用の初期データを入れる。既存のデータは全て消す（開発とテストでだけ動く）"""
        # 本番で打ち間違えても売上の記録と写真が消えないよう、--yes より先に止める
        if not (app.debug or app.testing):
            raise click.ClickException(
                "seed は開発環境（FLASK_DEBUG=1）でだけ実行できます"
            )
        if not yes and not click.confirm(
            "全データ（ユーザー、商品、注文）を消して入れ直します。よいですか？"
        ):
            raise click.Abort()
        run_seed()
        click.echo(
            f"ユーザー {len(SEED_USERS)} 人、商品 {len(SEED_PRODUCTS)} 件を入れました"
            "（パスワードは全員 password）"
        )
