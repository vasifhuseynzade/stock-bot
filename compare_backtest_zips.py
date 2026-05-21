from __future__ import annotations

import argparse
import json
import os
import zipfile
from typing import Any, Dict, List

import pandas as pd


def read_summary(zip_path: str) -> Dict[str, Any]:
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if "summary.json" in names:
            data = json.loads(z.read("summary.json").decode("utf-8"))
            data["zip"] = os.path.basename(zip_path)
            return data
        if "variant_compare.csv" in names:
            df = pd.read_csv(z.open("variant_compare.csv"))
            rows = []
            for _, row in df.iterrows():
                d = row.to_dict()
                d["zip"] = os.path.basename(zip_path)
                rows.append(d)
            return {"variant_rows": rows, "zip": os.path.basename(zip_path)}
        if "walkforward_summary.json" in names:
            data = json.loads(z.read("walkforward_summary.json").decode("utf-8"))
            data["zip"] = os.path.basename(zip_path)
            return data
    return {"zip": os.path.basename(zip_path), "error": "No supported summary file found"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("zips", nargs="+", help="Backtest / variant / walkforward zip files")
    parser.add_argument("--out", default="comparison_summary.csv")
    args = parser.parse_args()

    flat_rows: List[Dict[str, Any]] = []
    other_rows: List[Dict[str, Any]] = []

    for path in args.zips:
        summary = read_summary(path)
        if "variant_rows" in summary:
            flat_rows.extend(summary["variant_rows"])
        else:
            other_rows.append(summary)

    if flat_rows:
        df = pd.DataFrame(flat_rows)
        sort_cols = [c for c in ["profit_factor", "total_return_pct", "avg_r"] if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols, ascending=False)
        df.to_csv(args.out, index=False)
        print(df.to_string(index=False))
        print(f"\nSaved: {args.out}")

    if other_rows:
        print("\nOTHER SUMMARIES")
        print(json.dumps(other_rows, indent=2, default=str))


if __name__ == "__main__":
    main()
