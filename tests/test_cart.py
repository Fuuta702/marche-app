import pytest

from marche.models import Product


def cart_of(client):
    with client.session_transaction() as sess:
        return dict(sess.get("cart", {}))


def set_cart(client, cart):
    with client.session_transaction() as sess:
        sess["cart"] = cart


@pytest.fixture
def apple(producer, make_product):
    return make_product(producer, name="りんご", price=1200, stock=20)


# ---- 追加 ----


def test_未ログインでもカートに入れられる(client, apple):
    response = client.post(f"/cart/add/{apple.id}", data={"quantity": "2"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    assert cart_of(client) == {str(apple.id): 2}


def test_同じ商品を足すと数量が合算される(client, apple):
    client.post(f"/cart/add/{apple.id}", data={"quantity": "2"})
    client.post(f"/cart/add/{apple.id}", data={"quantity": "3"})
    assert cart_of(client) == {str(apple.id): 5}


def test_合計が10個を超える追加は弾く(client, apple):
    client.post(f"/cart/add/{apple.id}", data={"quantity": "8"})
    response = client.post(
        f"/cart/add/{apple.id}", data={"quantity": "3"}, follow_redirects=True
    )
    assert "1〜10 個にしてください" in response.text
    assert cart_of(client) == {str(apple.id): 8}


def test_いま買える数を超える追加は弾く(
    client, customer, producer, make_product, make_order
):
    product = make_product(producer, stock=5)
    make_order(customer, [(product, 3)])  # 支払い待ちで 3 個押さえられている
    client.post(f"/cart/add/{product.id}", data={"quantity": "3"})
    assert cart_of(client) == {}
    client.post(f"/cart/add/{product.id}", data={"quantity": "2"})
    assert cart_of(client) == {str(product.id): 2}


def test_売り切れの商品は追加できない(client, producer, make_product):
    product = make_product(producer, stock=0)
    response = client.post(
        f"/cart/add/{product.id}", data={"quantity": "1"}, follow_redirects=True
    )
    assert "売り切れ" in response.text
    assert cart_of(client) == {}


@pytest.mark.parametrize("quantity", ["0", "-1", "abc", ""])
def test_不正な数量は追加できない(client, apple, quantity):
    client.post(f"/cart/add/{apple.id}", data={"quantity": quantity})
    assert cart_of(client) == {}


def test_非公開や存在しない商品は追加できない(client, producer, make_product):
    hidden = make_product(producer, is_published=False)
    assert (
        client.post(f"/cart/add/{hidden.id}", data={"quantity": "1"}).status_code == 404
    )
    assert client.post("/cart/add/9999", data={"quantity": "1"}).status_code == 404
    assert cart_of(client) == {}


def test_追加はGETでは使えない(client, apple):
    assert client.get(f"/cart/add/{apple.id}").status_code == 405


# ---- 表示と変更 ----


def test_カートに単価と小計と合計が出る(client, producer, apple, make_product):
    miso = make_product(producer, name="味噌", price=1300, stock=10)
    set_cart(client, {str(apple.id): 2, str(miso.id): 1})
    text = client.get("/cart/").text
    assert "りんご" in text and "味噌" in text
    assert "1,200円" in text
    assert "2,400円" in text  # りんごの小計
    assert "3,700円" in text  # 合計


def test_空のカート(client):
    assert "カートは空です" in client.get("/cart/").text


def test_数量を変えられる(client, apple):
    set_cart(client, {str(apple.id): 2})
    client.post(f"/cart/update/{apple.id}", data={"quantity": "5"})
    assert cart_of(client) == {str(apple.id): 5}


@pytest.mark.parametrize("quantity", ["0", "-3"])
def test_0以下にすると外れる(client, apple, quantity):
    set_cart(client, {str(apple.id): 2})
    client.post(f"/cart/update/{apple.id}", data={"quantity": quantity})
    assert cart_of(client) == {}


@pytest.mark.parametrize("quantity", ["11", "abc"])
def test_不正な数量には変えられない(client, apple, quantity):
    set_cart(client, {str(apple.id): 2})
    client.post(f"/cart/update/{apple.id}", data={"quantity": quantity})
    assert cart_of(client) == {str(apple.id): 2}


def test_非公開になった商品は表示されずカートから外れる(client, apple):
    set_cart(client, {str(apple.id): 2, "9999": 1})
    apple_id = apple.id
    from marche.extensions import db

    db.session.get(Product, apple_id).is_published = False
    db.session.commit()
    text = client.get("/cart/").text
    assert "りんご" not in text
    assert "カートは空です" in text
    assert cart_of(client) == {}


def test_改ざんされたセッションのカートは無視する(client, apple):
    set_cart(client, {"abc": 1, str(apple.id): "2", "": True})
    assert "カートは空です" in client.get("/cart/").text


# ---- 画面 ----


def test_未ログインならログインして注文するリンク(client, apple):
    set_cart(client, {str(apple.id): 1})
    text = client.get("/cart/").text
    assert "ログインして注文する" in text
    assert 'href="/login?next=/cart/"' in text
    assert "注文して支払いへ進む" not in text


def test_ログイン中なら注文ボタン(client, customer, login, apple):
    login(customer)
    set_cart(client, {str(apple.id): 1})
    assert "注文して支払いへ進む" in client.get("/cart/").text


def test_ヘッダーのカートのリンクは誰にでも出る(client):
    assert 'href="/cart/"' in client.get("/").text


def test_詳細の数量はいま買える数と10の小さい方まで(client, producer, make_product):
    few = make_product(producer, name="少ない", stock=3)
    many = make_product(producer, name="多い", stock=50)
    few_text = client.get(f"/products/{few.id}").text
    assert '<option value="3">' in few_text
    assert '<option value="4">' not in few_text
    many_text = client.get(f"/products/{many.id}").text
    assert '<option value="10">' in many_text
    assert '<option value="11">' not in many_text


def test_売り切れなら詳細にカートのボタンが出ない(client, producer, make_product):
    product = make_product(producer, stock=0)
    assert "カートに入れる" not in client.get(f"/products/{product.id}").text


def test_追加で数量を減らすことはできない(client, apple):
    set_cart(client, {str(apple.id): 5})
    client.post(f"/cart/add/{apple.id}", data={"quantity": "-3"})
    assert cart_of(client) == {str(apple.id): 5}


def test_カートの0以下の数量は表示しない(client, apple):
    set_cart(client, {str(apple.id): -2})
    text = client.get("/cart/").text
    assert "カートは空です" in text
    assert "-2,400円" not in text
