"""GET /api/v1/products and their images, served from the in-memory catalogue."""

from __future__ import annotations

import logging
from pathlib import PurePath
from typing import Annotated

from azure.core.exceptions import AzureError
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.api.app.contracts import ProductOut
from src.api.app.dependencies import get_services
from src.api.app.services import Services

logger = logging.getLogger(__name__)
router = APIRouter(tags=["products"])

IMAGE_CACHE = "public, max-age=86400"
# Explicit, not mimetypes: the slim Linux image knows no ".webp", so the same code that passed on a
# Mac served WebP images as application/octet-stream in the container (day 7).
IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".png": "image/png",
}


@router.get("/products", response_model=list[ProductOut])
def list_products(
    request: Request, services: Annotated[Services, Depends(get_services)]
) -> list[ProductOut]:
    return [
        ProductOut(
            **product.model_dump(
                include={
                    "product_id",
                    "name",
                    "category",
                    "description",
                    "ingredients",
                    "allergens",
                    "may_contain",
                    "price",
                    "currency",
                    "rating",
                }
            ),
            image_url=str(request.url_for("product_image", product_id=product.product_id)),
        )
        for product in services.catalog.products
    ]


@router.get("/products/{product_id}/image", name="product_image")
def product_image(
    product_id: str, services: Annotated[Services, Depends(get_services)]
) -> Response:
    """The storage account is private: the API reads the blob with its managed identity. Only a
    catalogue product's own image can be asked for, so no path ever reaches Blob Storage."""
    product = services.catalog.get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="unknown product")
    try:
        data = services.images.read(product.image_file)
    except AzureError:
        logger.exception("image read failed", extra={"product_id": product_id})
        raise HTTPException(status_code=503, detail="image temporarily unavailable") from None
    # The real type of the file: five of the eighteen images are WebP, all were served as JPEG.
    media_type = IMAGE_TYPES.get(
        PurePath(product.image_file).suffix.lower(), "application/octet-stream"
    )
    return Response(data, media_type=media_type, headers={"Cache-Control": IMAGE_CACHE})
