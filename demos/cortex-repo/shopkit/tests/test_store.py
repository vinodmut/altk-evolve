from shopkit import store
from shopkit.models import Order, Product, User


def test_user_round_trip(db):
    user_id = store.insert(db, User(id=None, email="ada@example.com", name="Ada"))
    assert store.get(db, User, user_id) == User(id=user_id, email="ada@example.com", name="Ada")


def test_order_defaults_to_pending(db):
    user_id = store.insert(db, User(id=None, email="lin@example.com", name="Lin"))
    order_id = store.insert(db, Order(id=None, user_id=user_id, total_cents=1250))
    assert store.get(db, Order, order_id).status == "pending"


def test_product_update(db):
    product_id = store.insert(db, Product(id=None, sku="MUG-1", title="Mug", price_cents=900))
    product = store.get(db, Product, product_id)
    product.price_cents = 800
    store.update(db, product)
    assert store.get(db, Product, product_id).price_cents == 800
