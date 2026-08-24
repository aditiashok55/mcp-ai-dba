INSERT INTO customers (name, email)
VALUES
    ('Alice Johnson', 'alice@example.com'),
    ('Bob Smith', 'bob@example.com'),
    ('Charlie Brown', 'charlie@example.com'),
    ('Diana Wilson', 'diana@example.com'),
    ('Eva Davis', 'eva@example.com');

INSERT INTO products (name, category, price, stock)
VALUES
    ('Laptop', 'Electronics', 1200.00, 15),
    ('Keyboard', 'Electronics', 80.00, 50),
    ('Mouse', 'Electronics', 40.00, 100),
    ('Monitor', 'Electronics', 300.00, 25),
    ('Desk', 'Furniture', 450.00, 10);

INSERT INTO orders (customer_id, amount, status)
VALUES
    (1, 1280.00, 'completed'),
    (2, 340.00, 'completed'),
    (3, 450.00, 'pending'),
    (1, 80.00, 'completed'),
    (4, 1200.00, 'processing');

INSERT INTO order_items (order_id, product_id, quantity)
VALUES
    (1, 1, 1),
    (1, 2, 1),
    (2, 4, 1),
    (2, 3, 1),
    (3, 5, 1),
    (4, 2, 1),
    (5, 1, 1);