"""Step 1, prep_data: raw sales -> baskets of catalogue products.

python -m reco.prep_data --sales_dir <data asset> --output_dir <baskets>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import tracking
from .data import load_baskets
from .io import write_baskets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sales_dir", type=Path, required=True)
    parser.add_argument("--output_dir", type=Path, required=True)
    args = parser.parse_args()

    with tracking.run("prep_data"):
        baskets = load_baskets(args.sales_dir)
        multi = [b for b in baskets if len(b.items) >= 2]
        write_baskets(baskets, args.output_dir)
        tracking.metrics(
            {
                "baskets": len(baskets),
                "baskets_with_two_items_or_more": len(multi),
                "mean_items_per_multi_basket": sum(len(b.items) for b in multi) / len(multi),
                "max_items_per_basket": max(len(b.items) for b in baskets),
            }
        )
        tracking.tags({"first_date": baskets[0].date, "last_date": baskets[-1].date})
        print(f"{len(baskets)} baskets, {len(multi)} with two items or more")


if __name__ == "__main__":
    main()
