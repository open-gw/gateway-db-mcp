-- Layer 1: SELECT-only application credential for the MariaDB bench instance.
-- Schema and data are mounted separately (schema.sql + data-a.sql) so this
-- engine reuses the same MySQL fixtures. Create the user here rather than via
-- MARIADB_USER so the official entrypoint cannot GRANT ALL to the bridge user.
CREATE USER IF NOT EXISTS 'readonly_user'@'%' IDENTIFIED BY 'readonlypassword';
GRANT SELECT ON testdb.* TO 'readonly_user'@'%';
FLUSH PRIVILEGES;

-- Expected: GRANT USAGE ON *.* / GRANT SELECT ON `testdb`.*
-- Verify after init: SHOW GRANTS FOR 'readonly_user'@'%';
