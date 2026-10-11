#!/usr/bin/env python3
"""Validate generated NORWARE CSV file counts, keys, relationships and domain rules."""
import csv
from pathlib import Path
P=Path(__file__).resolve().parent
def rows(name):
    with (P/name).open(newline="",encoding="utf-8") as f: return list(csv.DictReader(f))
products=rows("products.csv"); taxonomy=rows("taxonomy.csv"); warehouses=rows("warehouses.csv"); locations=rows("locations.csv"); inventory=rows("inventory.csv"); sources=rows("data_sources.csv")
assert len(products)==10_000, f"expected 10000 products; got {len(products)}"
assert all(p["data_origin"]=="GENERATED_EXAMPLE" and p["is_sourced"]=="false" and p["generated_seed"]=="20261010" for p in products)
assert len({p["product_id"] for p in products})==len(products)
assert len({p["sku"] for p in products})==len(products)
tax={t["category_code"] for t in taxonomy}; wh={w["warehouse_id"] for w in warehouses}; loc={l["location_id"] for l in locations}; prod={p["product_id"] for p in products}; src={s["source_id"] for s in sources}
assert all(p["category_code"] in tax and p["source_id"] in src for p in products)
assert all(l["warehouse_id"] in wh for l in locations)
assert len(inventory)==len(products) and all(i["product_id"] in prod and i["location_id"] in loc for i in inventory)
assert all(1<=int(p["access_priority"])<=5 and 1<=int(p["picking_priority"])<=5 for p in products)
assert all(float(p["length_cm"])>0 and float(p["width_cm"])>0 and float(p["height_cm"])>0 and float(p["weight_kg"])>0 for p in products)
assert all(float(p["max_temp_c"])>=float(p["min_temp_c"]) for p in products)
assert all(int(i["reserved_qty"])<=int(i["on_hand_qty"]) for i in inventory)
print(f"PASS: {len(products):,} generated examples; {len(taxonomy)} taxonomy nodes; {len(warehouses)} warehouses; {len(locations)} locations; {len(inventory):,} inventory rows; all tested keys and domain constraints valid.")
