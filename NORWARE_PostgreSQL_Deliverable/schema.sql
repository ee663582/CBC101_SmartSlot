-- NORWARE illustrative warehouse catalog. PostgreSQL 14+.
CREATE SCHEMA IF NOT EXISTS norware;
SET search_path = norware, public;

CREATE TABLE data_sources (
 source_id text PRIMARY KEY, source_name text NOT NULL, source_type text NOT NULL CHECK (source_type IN ('GENERATED','REFERENCE','SOURCED')),
 url text, license text NOT NULL, record_usage text NOT NULL, accessed_date date
);
CREATE TABLE taxonomy (
 category_code text PRIMARY KEY, category_name text NOT NULL, parent_code text REFERENCES taxonomy(category_code),
 level smallint NOT NULL CHECK (level IN (1,2)), CHECK ((level=1 AND parent_code IS NULL) OR (level=2 AND parent_code IS NOT NULL))
);
CREATE TABLE warehouses (
 warehouse_id text PRIMARY KEY, warehouse_name text NOT NULL, country_code char(2) NOT NULL,
 city text NOT NULL, storage_profile text NOT NULL, capacity_m3 numeric(12,2) NOT NULL CHECK(capacity_m3>0)
);
CREATE TABLE warehouse_locations (
 location_id text PRIMARY KEY, warehouse_id text NOT NULL REFERENCES warehouses,
 zone_code text NOT NULL, aisle text NOT NULL, bay text NOT NULL, level smallint NOT NULL CHECK(level>0),
 location_capacity_m3 numeric(12,2) NOT NULL CHECK(location_capacity_m3>0), storage_profile text NOT NULL,
 UNIQUE(warehouse_id,zone_code,aisle,bay,level)
);
CREATE TABLE products (
 product_id text PRIMARY KEY, sku text NOT NULL UNIQUE, product_name text NOT NULL,
 category_code text NOT NULL REFERENCES taxonomy(category_code), material_category text NOT NULL,
 unit_of_measure text NOT NULL CHECK(unit_of_measure IN ('each','case','bag','roll','pallet')),
 length_cm numeric(9,1) NOT NULL CHECK(length_cm>0), width_cm numeric(9,1) NOT NULL CHECK(width_cm>0), height_cm numeric(9,1) NOT NULL CHECK(height_cm>0),
 weight_kg numeric(10,3) NOT NULL CHECK(weight_kg>0), volume_l numeric(12,3) NOT NULL CHECK(volume_l>0),
 capacity_units_per_location integer NOT NULL CHECK(capacity_units_per_location>0),
 access_priority smallint NOT NULL CHECK(access_priority BETWEEN 1 AND 5), picking_priority smallint NOT NULL CHECK(picking_priority BETWEEN 1 AND 5),
 fragile boolean NOT NULL, hazard_level text NOT NULL CHECK(hazard_level IN ('LOW','MEDIUM','HIGH')),
 storage_condition text NOT NULL CHECK(storage_condition IN ('AMBIENT','CHILLED')),
 min_temp_c numeric(5,1) NOT NULL, max_temp_c numeric(5,1) NOT NULL CHECK(max_temp_c>=min_temp_c),
 data_origin text NOT NULL CHECK(data_origin IN ('GENERATED_EXAMPLE','SOURCED')),
 source_id text NOT NULL REFERENCES data_sources(source_id), source_record_id text,
 is_sourced boolean NOT NULL, generated_seed integer,
 CHECK ((data_origin='GENERATED_EXAMPLE' AND is_sourced=false AND generated_seed IS NOT NULL) OR (data_origin='SOURCED' AND is_sourced=true AND source_record_id IS NOT NULL))
);
CREATE TABLE inventory (
 inventory_id text PRIMARY KEY, product_id text NOT NULL REFERENCES products,
 location_id text NOT NULL REFERENCES warehouse_locations, on_hand_qty integer NOT NULL CHECK(on_hand_qty>=0),
 reserved_qty integer NOT NULL CHECK(reserved_qty>=0 AND reserved_qty<=on_hand_qty), reorder_point integer NOT NULL CHECK(reorder_point>=0),
 lot_code text NOT NULL, expiry_date date, UNIQUE(product_id,location_id,lot_code)
);
CREATE INDEX products_category_idx ON products(category_code);
CREATE INDEX products_pick_priority_idx ON products(picking_priority,access_priority);
CREATE INDEX inventory_location_idx ON inventory(location_id);
