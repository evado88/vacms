# Generate Cluster IDs

The management command manages ward cluster codes in the `ClusterLocationCodes` table. It builds codes from 4-digit bases that pass several pattern filters, then appends two checksum letters derived from weighted sums mapped via a fixed lookup. All generated codes are guaranteed to be globally unique.

## Prerequisites

Ensure the `wards.csv` file exists at `va_explorer/static/data/wards.csv` with columns: `ward`, `ward_code`.

## Usage

The command supports three operational modes:

### `--initial` - Initial Setup

Loads ward data from CSV, creates the table if needed (with automatic migrations), and generates cluster codes for all wards.

```bash
python manage.py generate_ward_codes --initial
```

**Behavior:**
- Checks if `ClusterLocationCodes` table exists
- If table missing: attempts `makemigrations` and `migrate` automatically
- If migration fails: prompts user to run migrations manually
- Clears any existing data in the table
- Loads all wards from `wards.csv`
- Generates and assigns unique `cluster_code` for each ward
- Reports completion summary

### `--update` - Sync New Wards

Compares CSV against existing table data and adds new wards with cluster codes.

```bash
python manage.py generate_ward_codes --update
```

**Behavior:**
- Reads existing ward names from `ClusterLocationCodes`
- Identifies new wards in CSV not present in table
- Adds new wards with their `ward_code` from CSV
- Generates and assigns unique `cluster_code` for new wards only
- Leaves existing wards unchanged
- Reports count of new wards added

### `--update-codes` - Fill Missing Codes

Finds wards in the table without cluster codes and generates codes for them.

```bash
python manage.py generate_ward_codes --update-codes
```

**Behavior:**
- Queries `ClusterLocationCodes` for rows with null or empty `cluster_code`
- Generates and assigns unique codes for these wards
- Does not load CSV or modify existing codes
- Reports count of wards updated

## Notes

- Only one flag can be used at a time
- All generated `cluster_code` values are checked for global uniqueness
- Bulk operations use batch sizes of 1000 for efficiency
- CSV path: `va_explorer/static/data/wards.csv`
