import os

import pytest

from marche import create_app
from marche.extensions import db
from marche.models import Order, OrderItem, Product, Role, User
from marche.uploads import save_image

# ---- 一覧 ----


def test_一覧には公開中の商品だけが出る(client, producer, make_product):
    make_product(producer, name="公開のりんご", is_published=True)
    make_product(producer, name="非公開のなし", is_published=False)
    response = client.get("/")
    assert response.status_code == 200
    assert "公開のりんご" in response.text
    assert "非公開のなし" not in response.text


def test_一覧に価格と生産者名と在庫が出る(client, producer, make_product):
    make_product(producer, name="ポーク", price=1200, stock=8)
    text = client.get("/").text
    assert "1,200円" in text
    assert producer.name in text
    assert "在庫 8" in text


def test_在庫0は売り切れ(client, producer, make_product):
    make_product(producer, stock=0)
    text = client.get("/").text
    assert "売り切れ" in text
    assert "在庫 0" not in text


def test_一覧の在庫は支払い待ちの分を引く(
    client, customer, producer, make_product, make_order
):
    product = make_product(producer, stock=5)
    make_order(customer, [(product, 2)])
    assert "在庫 3" in client.get("/").text


def test_公開商品が無ければその旨を出す(client):
    assert "いま出品中の商品はありません" in client.get("/").text


# ---- 詳細 ----


def test_公開中の商品の詳細は誰でも見られる(client, producer, make_product):
    product = make_product(
        producer, name="味噌", price=1300, stock=12, description="1行目\n2行目"
    )
    response = client.get(f"/products/{product.id}")
    assert response.status_code == 200
    assert "味噌" in response.text
    assert "1,300円" in response.text
    assert producer.name in response.text
    assert "いま買える数: 12" in response.text
    assert "1行目\n2行目" in response.text  # 改行は消さずに CSS で保つ


def test_詳細の紹介文はエスケープされる(client, producer, make_product):
    product = make_product(producer, description="<script>alert(1)</script>")
    text = client.get(f"/products/{product.id}").text
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


def test_詳細で在庫0は売り切れ(client, producer, make_product):
    product = make_product(producer, stock=0)
    assert "売り切れ" in client.get(f"/products/{product.id}").text


@pytest.fixture
def hidden(producer, make_product):
    return make_product(producer, name="試作品", is_published=False)


def test_非公開の詳細は未ログインには404(client, hidden):
    assert client.get(f"/products/{hidden.id}").status_code == 404


def test_非公開の詳細は購入者には404(client, customer, login, hidden):
    login(customer)
    assert client.get(f"/products/{hidden.id}").status_code == 404


def test_非公開の詳細は別の生産者には404(client, other_producer, login, hidden):
    login(other_producer)
    assert client.get(f"/products/{hidden.id}").status_code == 404


def test_非公開の詳細は出品した生産者には見える(client, producer, login, hidden):
    login(producer)
    response = client.get(f"/products/{hidden.id}")
    assert response.status_code == 200
    assert "非公開の商品です" in response.text


def test_非公開の詳細は管理者には見える(client, admin, login, hidden):
    login(admin)
    assert client.get(f"/products/{hidden.id}").status_code == 200


def test_存在しない商品は404(client):
    assert client.get("/products/9999").status_code == 404


# ---- flask seed ----


def run_seed(app, *args):
    return app.test_cli_runner().invoke(args=["seed", *args])


def test_seedで5人と8商品が入る(app):
    result = run_seed(app, "--yes")
    assert result.exit_code == 0
    users = db.session.scalars(db.select(User)).all()
    assert len(users) == 5
    roles = sorted(user.role for user in users)
    assert roles == sorted(
        [Role.ADMIN, Role.PRODUCER, Role.PRODUCER, Role.CUSTOMER, Role.CUSTOMER]
    )
    products = db.session.scalars(db.select(Product)).all()
    assert len(products) == 8
    assert sum(p.is_published for p in products) == 7


def test_seedは何度実行しても入れ直す(app, customer, producer, make_product):
    make_product(producer, name="前からある商品")
    run_seed(app, "--yes")
    run_seed(app, "--yes")
    assert len(db.session.scalars(db.select(User)).all()) == 5
    assert len(db.session.scalars(db.select(Product)).all()) == 8
    assert (
        db.session.scalar(db.select(Product).filter_by(name="前からある商品")) is None
    )


def test_seedは確認でnoなら何もしない(app, customer):
    result = app.test_cli_runner().invoke(args=["seed"], input="n\n")
    assert result.exit_code != 0
    assert db.session.scalars(db.select(User)).all() == [customer]


def test_seedのユーザーでログインできる(app, client):
    run_seed(app, "--yes")
    response = client.post(
        "/login", data={"email": "producer1@example.com", "password": "password"}
    )
    assert response.headers["Location"] == "/producer/products"


def test_seedは注文があっても消して入れ直す(
    app, customer, producer, make_product, make_order
):
    product = make_product(producer)
    make_order(customer, [(product, 2)])
    result = run_seed(app, "--yes")
    assert result.exit_code == 0
    assert db.session.scalars(db.select(Order)).all() == []
    assert db.session.scalars(db.select(OrderItem)).all() == []
    assert len(db.session.scalars(db.select(Product)).all()) == 8


def test_seedは商品の写真のファイルも消す(app, producer, make_product):
    filename = save_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "image/png")
    make_product(producer, image_filename=filename)
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    assert os.path.exists(path)
    run_seed(app, "--yes")
    assert not os.path.exists(path)


def test_seedは開発環境でなければ動かない(monkeypatch, tmp_path):
    # 本番と同じく debug も testing も無い app を作る
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    prod_app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test-secret",
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        }
    )
    with prod_app.app_context():
        db.create_all()
        user = User(name="本番の人", email="real@example.com", role=Role.CUSTOMER)
        user.set_password("password")
        db.session.add(user)
        db.session.commit()
        result = prod_app.test_cli_runner().invoke(args=["seed", "--yes"])
        assert result.exit_code != 0
        assert "開発環境" in result.output
        assert [u.email for u in db.session.scalars(db.select(User))] == [
            "real@example.com"
        ]
        db.session.remove()


def test_一覧は新しい順に並ぶ(client, producer, make_product):
    make_product(producer, name="先に登録した商品")
    make_product(producer, name="後に登録した商品")
    text = client.get("/").text
    assert text.index("後に登録した商品") < text.index("先に登録した商品")
