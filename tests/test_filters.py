import pytest


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, "0円"),
        (450, "450円"),
        (1200, "1,200円"),
        (100000, "100,000円"),
        (None, ""),
    ],
)
def test_yenフィルター(app, value, expected):
    yen = app.jinja_env.filters["yen"]
    assert yen(value) == expected
