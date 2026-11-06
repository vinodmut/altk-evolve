CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, name TEXT);
CREATE TABLE products (id INTEGER PRIMARY KEY, sku TEXT, title TEXT, price_cents INTEGER);
CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, total_cents INTEGER);
