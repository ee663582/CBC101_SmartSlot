# NORWARE Warehouse Operations

NORWARE is a local warehouse management demo built in Python with SQLite. It includes a warehouse dashboard, staff login flow, product and shelf lookup, barcode workflows, allocation and movement tracking, waste-stream rules, and an optional AI assistant.

This project is intended for local demo use on `127.0.0.1` and is not a production deployment.

## Features

- Warehouse entry screen and local demo mode
- Staff login with password reset flow
- Product lookup by barcode, SKU, or name
- Shelf lookup and barcode label generation
- Capacity, dimension, and compatibility checks
- Allocation suggestions and staff-confirmed placement
- Receiving, transfer, dispatch, adjustment, and quarantine workflows
- Waste stream handling with restricted storage rules
- Audit log for append-only operational history
- Optional AI guidance via `OPENAI_API_KEY`
- Sample warehouse dataset and generated product catalog

## Project files

- `norware_app.py` — main local application server and data layer
- `norware_web.html` — browser UI served by the app
- `norware_shelf_manager.html` — standalone shelf-management prototype
- `schema.sql` and `load.sql` — PostgreSQL schema/data-loading sample
- `generate_data.py` and `validate_data.py` — dataset generation and validation
- `waste_config.json` — waste-stream configuration
- `products.csv`, `locations.csv`, `warehouses.csv`, etc. — sample data

## Requirements

- Python 3
- No third-party packages required for the local SQLite app

## Run locally

From the project directory:

```bash
python3 norware_app.py
```

Then open:

```text
http://127.0.0.1:8765
```

On first run, the app creates `norware.sqlite3` in the project folder and imports sample data.

## Demo access

- Continue without login for the local demo experience
- Staff username: `staff.demo`
- Staff password: `123`

The first login requires a password change. Demo data is isolated and reset when the server stops.

## Optional configuration

You can override the demo account settings before first launch:

```bash
export NORWARE_DEMO_USER=staff.demo
export NORWARE_DEMO_PASSWORD=123
```

To enable AI assistant guidance:

```bash
export OPENAI_API_KEY=your-key-here
export NORWARE_AI_MODEL=gpt-6-astra
```

## Regenerate sample data

```bash
python3 generate_data.py
python3 validate_data.py
```

## PostgreSQL sample deliverable

The repo also includes a PostgreSQL-oriented sample dataset for schema and CSV loading:

```bash
psql -d YOUR_DATABASE -f schema.sql
psql -d YOUR_DATABASE -f load.sql
```

## Important notes

- This app is intended for local development and demo use only.
- Keep the server bound to `localhost`/`127.0.0.1`.
- Waste stream names and safety rules are example-only configurations and should be reviewed against local policies before real operational use.
- The generated sample data is synthetic and should not be treated as real supplier or product inventory.

## Quick start summary

```bash
cd /Users/ayesha./Downloads/1225-main
python3 norware_app.py
```

Then visit:

```text
http://127.0.0.1:8765
```
