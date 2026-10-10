-- Run with psql from the outputs directory after schema.sql.
SET search_path = norware, public;
\copy data_sources FROM 'data_sources.csv' WITH (FORMAT csv, HEADER true)
\copy taxonomy FROM 'taxonomy.csv' WITH (FORMAT csv, HEADER true)
\copy warehouses FROM 'warehouses.csv' WITH (FORMAT csv, HEADER true)
\copy warehouse_locations FROM 'locations.csv' WITH (FORMAT csv, HEADER true)
\copy products FROM 'products.csv' WITH (FORMAT csv, HEADER true)
\copy inventory FROM 'inventory.csv' WITH (FORMAT csv, HEADER true)

-- Import sanity checks; each should return the expected count / zero.
SELECT count(*) AS product_count FROM products; -- 10000
SELECT data_origin, is_sourced, count(*) FROM products GROUP BY 1,2;
SELECT count(*) AS invalid_inventory FROM inventory WHERE reserved_qty > on_hand_qty;
SELECT count(*) AS orphan_inventory FROM inventory i LEFT JOIN products p USING(product_id) LEFT JOIN warehouse_locations l USING(location_id) WHERE p.product_id IS NULL OR l.location_id IS NULL;
