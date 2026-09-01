-- Shared schema for all three database instances (mysql-a, mysql-b, postgres).
-- IMPORTANT: reconcile this with your existing sidecar/init-mysql.sql before
-- running E3. The reproducibility experiment is only meaningful if mysql-a and
-- mysql-b have byte-identical schemas and different data.
CREATE TABLE customers (
  id            INT PRIMARY KEY,
  name          VARCHAR(120) NOT NULL,
  email         VARCHAR(200),
  country_code  CHAR(2)
);

CREATE TABLE products (
  id        INT PRIMARY KEY,
  sku       VARCHAR(64) NOT NULL,
  name      VARCHAR(200) NOT NULL,
  price     DECIMAL(10,2) NOT NULL
);

CREATE TABLE orders (
  id           INT PRIMARY KEY,
  customer_id  INT NOT NULL,
  status       VARCHAR(32) NOT NULL,
  total        DECIMAL(10,2) NOT NULL,
  placed_at    TIMESTAMP NULL
);

-- Not in allowedTables. Used to demonstrate the Layer 3 discovery filter,
-- and to exercise the documented POST /query confidentiality gap.
CREATE TABLE internal_audit (
  id       INT PRIMARY KEY,
  actor    VARCHAR(120),
  action   VARCHAR(120)
);
