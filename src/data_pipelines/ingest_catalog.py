"""Load the catalogue into Azure (roadmap task 2.2).

    data/raw/products.jsonl  ->  Blob Storage (images)  +  Cosmos DB `products` (documents)

Three properties matter more than the loading itself:

- **Keyless.** Both clients authenticate with DefaultAzureCredential; the accounts have their
  keys disabled, so there is no other way in.
- **Idempotent.** An image whose MD5 already matches the blob is skipped, and documents are
  upserted by id. Running twice changes nothing the second time.
- **Synchronising.** A product removed from the catalogue is deleted from Cosmos, otherwise the
  assistant would keep selling it (ADR-004: the catalogue is the only source of truth).

Run from the repository root:
    python -m src.data_pipelines.ingest_catalog --dry-run    # plan only, writes nothing
    python -m src.data_pipelines.ingest_catalog
"""

from __future__ import annotations

import argparse
import hashlib
import mimetypes
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.cosmos import ContainerProxy, CosmosClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContainerClient, ContentSettings

from src.api.core.schemas import Product
from src.api.core.settings import get_settings
from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl

IMAGES_DIR = Path("data/raw/images")


@dataclass
class Report:
    uploaded: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    upserted: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


def content_type_for(path: Path) -> str:
    """Browsers need the right type to display an image served from Blob Storage."""
    if path.suffix.lower() == ".webp":
        # Not registered in every Python mimetypes table.
        return "image/webp"
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed is None or not guessed.startswith("image/"):
        raise ValueError(f"{path.name} is not a recognised image type")
    return guessed


def md5_of(data: bytes) -> bytes:
    # MD5 is what Blob Storage stores as Content-MD5: an integrity check, not a security control.
    return hashlib.md5(data, usedforsecurity=False).digest()


def stale_documents(existing: Iterable[dict[str, Any]], keep: set[str]) -> list[dict[str, Any]]:
    """Documents in Cosmos whose id is no longer in the catalogue."""
    return sorted((doc for doc in existing if doc["id"] not in keep), key=lambda d: d["id"])


def sync_images(
    container: ContainerClient, products: list[Product], report: Report, dry_run: bool
) -> None:
    for product in products:
        path = IMAGES_DIR / product.image_file
        if not path.is_file():
            raise FileNotFoundError(f"image missing for {product.product_id}: {path}")
        data = path.read_bytes()
        local_md5 = md5_of(data)

        blob = container.get_blob_client(product.image_file)
        try:
            remote_md5 = blob.get_blob_properties().content_settings.content_md5
        except ResourceNotFoundError:
            remote_md5 = None

        if remote_md5 is not None and bytes(remote_md5) == local_md5:
            report.unchanged.append(product.image_file)
            continue
        if not dry_run:
            blob.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(
                    content_type=content_type_for(path), content_md5=bytearray(local_md5)
                ),
            )
        report.uploaded.append(product.image_file)


def sync_documents(
    container: ContainerProxy, products: list[Product], report: Report, dry_run: bool
) -> None:
    for product in products:
        # mode="json" writes the price as the string "4.75": Cosmos stores numbers as binary
        # floats, and a price must come back exactly as it went in.
        if not dry_run:
            container.upsert_item(product.model_dump(mode="json"))
        report.upserted.append(product.product_id)

    existing = container.query_items(
        "SELECT c.id, c.category FROM c", enable_cross_partition_query=True
    )
    for doc in stale_documents(existing, keep={p.product_id for p in products}):
        if not dry_run:
            container.delete_item(item=doc["id"], partition_key=doc["category"])
        report.deleted.append(doc["id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.require("azure_storage_blob_endpoint", "azure_cosmos_endpoint")
    products = build_catalog(read_jsonl(RAW_PATH))
    credential = DefaultAzureCredential()
    report = Report()

    blob_service = BlobServiceClient(settings.azure_storage_blob_endpoint, credential=credential)
    sync_images(
        blob_service.get_container_client(settings.images_container), products, report, args.dry_run
    )

    cosmos = CosmosClient(settings.azure_cosmos_endpoint, credential=credential)
    container = cosmos.get_database_client(settings.azure_cosmos_database).get_container_client(
        settings.cosmos_products_container
    )
    sync_documents(container, products, report, args.dry_run)

    mode = "DRY RUN, nothing written" if args.dry_run else "applied"
    print(f"Catalogue ingestion ({mode})")
    print(f"  images uploaded : {len(report.uploaded):2}  {', '.join(report.uploaded)}")
    print(f"  images unchanged: {len(report.unchanged):2}")
    print(f"  documents upsert: {len(report.upserted):2}")
    print(f"  documents delete: {len(report.deleted):2}  {', '.join(report.deleted)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
