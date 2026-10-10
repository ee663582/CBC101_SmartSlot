#!/usr/bin/env python3
"""Deterministically generate NORWARE's illustrative warehouse catalog CSVs."""
import csv, random
from pathlib import Path

OUT = Path(__file__).resolve().parent
SEED = 20261010
N = 10_000

TAXONOMY = [
 ("FOOD","Food & beverages",["Grains & pulses","Packaged foods","Produce","Dairy","Frozen foods","Beverages"]),
 ("ELEC","Electrical & electronics",["Computers","Consumer electronics","Electrical components","Cables","Batteries"]),
 ("CHEM","Chemicals & process materials",["Industrial chemicals","Cleaning products","Paints & coatings","Laboratory supplies"]),
 ("PHAR","Healthcare & pharmaceuticals",["Medical devices","Pharmaceuticals","PPE","Diagnostic supplies"]),
 ("TEXT","Textiles & apparel",["Clothing","Footwear","Fabrics","Home textiles"]),
 ("AUTO","Automotive & transport",["Vehicle parts","Tires","Lubricants","Bicycle parts"]),
 ("BUILD","Construction & hardware",["Lumber","Cement & aggregates","Fasteners","Tools","Plumbing"]),
 ("HOME","Home & office",["Furniture","Stationery","Appliances","Housewares"]),
 ("AGRI","Agriculture & outdoor",["Seeds","Fertilizers","Animal feed","Garden supplies"]),
 ("PACK","Packaging & shipping",["Cartons","Pallets","Protective packaging","Containers"]),
 ("SPORT","Sport & recreation",["Exercise equipment","Outdoor gear","Toys & games"]),
 ("METAL","Metals & raw materials",["Steel products","Aluminum products","Copper products","Resins"]),
]
ADJECTIVES = ["Standard","Compact","Heavy-duty","Eco","Premium","Industrial","Retail","Bulk","Protective","Reinforced"]
WAREHOUSES = [
 ["WH001","Ambient General","US","Chicago","AMBIENT",25000],
 ["WH002","Food & Cold Chain","NL","Rotterdam","CHILLED",12000],
 ["WH003","Electronics & Secure","SG","Singapore","DRY_SECURE",9000],
 ["WH004","Hazardous Materials","DE","Hamburg","HAZMAT",7000],
 ["WH005","Building Materials","IN","Mumbai","BULK",30000],
 ["WH006","Healthcare Supply","JP","Osaka","CONTROLLED",8000],
]

def write(name, rows, fields):
    with (OUT/name).open("w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

def main():
    rng=random.Random(SEED)
    tax=[]; catmap={}
    for code,label,subs in TAXONOMY:
        tax.append(dict(category_code=code, category_name=label, parent_code="", level=1))
        for i,sub in enumerate(subs,1):
            subcode=f"{code}{i:02d}"; catmap[subcode]=(code,label,sub)
            tax.append(dict(category_code=subcode, category_name=sub, parent_code=code, level=2))
    products=[]
    for i in range(1,N+1):
        subcode=rng.choice(list(catmap)); code,label,sub=catmap[subcode]
        mass=round(rng.uniform(.05,800),3); length=round(rng.uniform(2,240),1); width=round(rng.uniform(2,180),1); height=round(rng.uniform(1,180),1)
        hazard="HIGH" if code=="CHEM" and rng.random()<.24 else ("MEDIUM" if code in ("CHEM","PHAR","ELEC") and rng.random()<.4 else "LOW")
        fragile=code in ("ELEC","PHAR","HOME","FOOD") and rng.random()<.28
        temp="CHILLED" if code=="FOOD" and sub in ("Dairy","Frozen foods","Produce") and rng.random()<.5 else "AMBIENT"
        products.append(dict(product_id=f"NW{i:05d}", sku=f"NW-{code}-{i:05d}", product_name=f"{rng.choice(ADJECTIVES)} {sub} item {i:05d}", category_code=subcode,
          material_category=label, unit_of_measure=rng.choice(["each","case","bag","roll","pallet"]), length_cm=length,width_cm=width,height_cm=height,
          weight_kg=mass, volume_l=round(length*width*height/1000,3), capacity_units_per_location=rng.randint(1,120), access_priority=rng.randint(1,5), picking_priority=rng.randint(1,5),
          fragile=str(fragile).lower(), hazard_level=hazard, storage_condition=temp, min_temp_c=(-25 if temp=="CHILLED" and sub=="Frozen foods" else (2 if temp=="CHILLED" else 10)),
          max_temp_c=(-18 if temp=="CHILLED" and sub=="Frozen foods" else (8 if temp=="CHILLED" else 35)), data_origin="GENERATED_EXAMPLE", source_id="SRC-GEN-001", source_record_id="",
          is_sourced="false", generated_seed=SEED))
    wh=[]; loc=[]
    for row in WAREHOUSES:
        wh.append(dict(zip(["warehouse_id","warehouse_name","country_code","city","storage_profile","capacity_m3"],row)))
        for a in range(1,5):
            for b in range(1,6):
                loc.append(dict(location_id=f"{row[0]}-A{a:02d}-B{b:02d}",warehouse_id=row[0],zone_code=f"Z{a:02d}",aisle=f"A{a:02d}",bay=f"B{b:02d}",level=1+(b%3),location_capacity_m3=round(row[5]/20,2),storage_profile=row[4]))
    inv=[]
    for p in products:
        w=rng.choice(WAREHOUSES); l=rng.choice([x for x in loc if x["warehouse_id"]==w[0]])
        inv.append(dict(inventory_id=f"INV-{p['product_id']}",product_id=p["product_id"],location_id=l["location_id"],on_hand_qty=rng.randint(0,250),reserved_qty=0,reorder_point=rng.randint(5,80),lot_code=f"LOT-{rng.randint(100000,999999)}",expiry_date="" if p["material_category"] not in ("Food & beverages","Healthcare & pharmaceuticals") else f"2027-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"))
    sources=[dict(source_id="SRC-GEN-001",source_name="NORWARE deterministic synthetic generator",source_type="GENERATED",url="",license="Not applicable; generated examples",record_usage="All 10,000 product records; no real-world product listing is asserted",accessed_date="2026-10-10"),
      dict(source_id="SRC-REF-001",source_name="GS1 Global Product Classification (reference only)",source_type="REFERENCE",url="https://www.gs1.org/standards/gpc",license="See provider terms",record_usage="Taxonomy context only; no records copied"),
      dict(source_id="SRC-REF-002",source_name="UN Central Product Classification (reference only)",source_type="REFERENCE",url="https://unstats.un.org/unsd/classifications/Econ/cpc",license="See provider terms",record_usage="Category coverage context only; no records copied")]
    write("taxonomy.csv",tax,["category_code","category_name","parent_code","level"])
    write("products.csv",products,list(products[0]))
    write("warehouses.csv",wh,list(wh[0])); write("locations.csv",loc,list(loc[0])); write("inventory.csv",inv,list(inv[0])); write("data_sources.csv",sources,list(sources[0]))
    print(f"Generated {len(products)} product rows, {len(tax)} taxonomy rows, {len(loc)} locations, {len(inv)} inventory rows (seed={SEED}).")

if __name__=="__main__": main()
