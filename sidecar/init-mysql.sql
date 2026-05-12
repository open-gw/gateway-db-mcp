-- gateway-db-mcp integration test schema
-- Run automatically by docker-compose on first start

CREATE TABLE IF NOT EXISTS orders (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT         NOT NULL,
    status      VARCHAR(50) NOT NULL DEFAULT 'pending',
    total       DECIMAL(10,2),
    created_at  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    id       INT AUTO_INCREMENT PRIMARY KEY,
    name     VARCHAR(255) NOT NULL,
    category VARCHAR(100),
    price    DECIMAL(10,2) NOT NULL,
    stock    INT          NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS customers (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    name       VARCHAR(255) NOT NULL,
    email      VARCHAR(255),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Seed data
INSERT INTO customers (name, email) VALUES
  ('Alice Smith',  'alice@example.com'),
  ('Bob Jones',    'bob@example.com'),
  ('Carol White',  'carol@example.com');

INSERT INTO products (name, category, price, stock) VALUES
  ('Widget A',   'electronics', 29.99, 150),
  ('Gadget B',   'electronics', 99.99, 42),
  ('Doohickey C','accessories', 14.99, 300);

INSERT INTO orders (customer_id, status, total) VALUES
  (1, 'completed', 29.99),
  (2, 'pending',   99.99),
  (1, 'completed', 44.98),
  (3, 'shipped',   14.99);

-- Grant read-only permissions to readonly_user
GRANT SELECT ON testdb.orders    TO 'readonly_user'@'%';
GRANT SELECT ON testdb.products  TO 'readonly_user'@'%';
GRANT SELECT ON testdb.customers TO 'readonly_user'@'%';
FLUSH PRIVILEGES;
