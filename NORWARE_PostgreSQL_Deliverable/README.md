# NORWARE warehouse database sample

This reproducible PostgreSQL starter deliverable contains **10,000 generated example product records** spanning 12 broad worldwide product groups and their subcategories. It is a structured sample, not an exhaustive global catalog. Product names, SKUs, dimensions, weights, safety attributes, priorities, lots and stock balances are synthetic; **zero rows are represented as real sourced products**. `data_sources.csv` distinguishes the generator from two taxonomy references. The references inform coverage only; no records were copied from them. Confirm actual dimensions, safe handling and compatibility against supplier documentation and applicable regulations before operational use.

## Files

- `schema.sql` — PostgreSQL tables, keys, indexes, and validation constraints.
- `load.sql` — `\copy` import commands and post-load checks.
- `generate_data.py` — deterministic CSV generator (Python 3 standard library; seed 20261010).
- `validate_data.py` — CSV count, key, relationship, and domain checks.
- `norware_shelf_manager.html` — standalone browser prototype for shelf management, ranked allocation, confirmation, capacity tracking, availability exceptions, and stock movements. It saves demo state in browser local storage.
- `products.csv` — 10,000 generated product examples, including dimensions, capacity, access/pick priority, fragility, hazard and storage attributes, and provenance.
- `taxonomy.csv`, `warehouses.csv`, `locations.csv`, `inventory.csv`, `data_sources.csv` — related import files.

## Regenerate and validate

From this directory, run:

```sh
python3 generate_data.py
python3 validate_data.py
```

Generation replaces the CSVs in this directory with the same records each time. Validation should print `PASS` with 10,000 products, 6 warehouses, 120 locations, and 10,000 inventory rows.

## Load into PostgreSQL

With PostgreSQL 14+ installed and a database created, run these from this directory:

```sh
psql -d YOUR_DATABASE -f schema.sql
psql -d YOUR_DATABASE -f load.sql
```

The schema creates a `norware` namespace. CSV files load in foreign-key order. The model records dimensions in centimetres, mass in kilograms, volume in litres, location/warehouse capacity in cubic metres, and temperature bounds in Celsius. Priorities use a 1–5 scale; document local business meaning before operational use. Hazard levels are illustrative labels, not regulatory classifications. Generated storage assignments are sample associations and do not assert physical compatibility or real facility capacity allocation.

## Data origin and limitations

Every row in `products.csv` has `data_origin=GENERATED_EXAMPLE`, `is_sourced=false`, `source_id=SRC-GEN-001`, and `generated_seed=20261010`. Reference sources are listed only for taxonomy context: [GS1 Global Product Classification](https://www.gs1.org/standards/gpc) and [UN Central Product Classification](https://unstats.un.org/unsd/classifications/Econ/cpc). Their current license/terms should be reviewed before importing data from either. No real product identifiers, supplier records, barcode records, or verified safety information are supplied here.
