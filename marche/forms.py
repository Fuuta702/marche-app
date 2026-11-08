from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileSize
from wtforms import (
    BooleanField,
    EmailField,
    IntegerField,
    PasswordField,
    RadioField,
    StringField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    Regexp,
)

from marche.models import Role

# email-validator パッケージを足さずに済むよう、形だけを正規表現で確かめる
EMAIL = Regexp(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="メールアドレスの形式で入力してください"
)


class RegisterForm(FlaskForm):
    name = StringField("名前", validators=[DataRequired(), Length(max=50)])
    email = EmailField(
        "メールアドレス", validators=[DataRequired(), EMAIL, Length(max=255)]
    )
    password = PasswordField("パスワード", validators=[DataRequired(), Length(min=8)])
    # 選択肢に admin を入れない。送られてきても RadioField の検証で弾かれる
    role = RadioField(
        "登録の種類",
        choices=[(Role.CUSTOMER, "購入者"), (Role.PRODUCER, "生産者")],
        default=Role.CUSTOMER,
    )


class LoginForm(FlaskForm):
    email = EmailField("メールアドレス", validators=[DataRequired(), EMAIL])
    password = PasswordField("パスワード", validators=[DataRequired()])


class CartQuantityForm(FlaskForm):
    # 数量だけを受け取る（金額は受け取らない）。上限は在庫で変わるのでビューで確かめる
    quantity = IntegerField(
        "数量", validators=[InputRequired(message="数量を入力してください")]
    )


class ProductForm(FlaskForm):
    # producer_id は持たない。出品者はログイン中のユーザーにする（フォームから受け取らない）
    name = StringField("商品名", validators=[DataRequired(), Length(max=50)])
    # 0 も入力として受け付けるため、DataRequired ではなく InputRequired を使う
    price = IntegerField(
        "価格（円・税込み）",
        validators=[
            InputRequired(),
            NumberRange(min=50, max=100000, message="50〜100,000円で入力してください"),
        ],
    )
    stock = IntegerField(
        "在庫数",
        validators=[
            InputRequired(),
            NumberRange(min=0, max=999, message="0〜999で入力してください"),
        ],
    )
    description = TextAreaField("紹介文", validators=[Optional(), Length(max=2000)])
    is_published = BooleanField("公開する")
    # 拡張子とサイズはここで確かめる。中身（先頭のバイト）はビューで uploads.detect_media_type を使う
    photo = FileField(
        "写真（JPEG / PNG / WebP、5MB まで）",
        validators=[
            FileAllowed(
                ["jpg", "jpeg", "png", "webp"],
                "JPEG / PNG / WebP の画像を選んでください",
            ),
            FileSize(
                max_size=5 * 1024 * 1024, message="5MB 以下の画像を選んでください"
            ),
        ],
    )
