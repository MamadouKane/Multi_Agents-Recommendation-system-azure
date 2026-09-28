"""Pure parts of the ingestion pipeline. The Azure calls themselves are covered by running it."""

from pathlib import Path

import pytest

from src.data_pipelines.ingest_catalog import content_type_for, md5_of, stale_documents


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Latte.jpg", "image/jpeg"), ("SavoryScone.webp", "image/webp"), ("a.PNG", "image/png")],
)
def test_content_type(name, expected):
    assert content_type_for(Path(name)) == expected


def test_content_type_rejects_a_non_image():
    with pytest.raises(ValueError):
        content_type_for(Path("notes.txt"))


def test_md5_is_stable_and_sixteen_bytes():
    assert md5_of(b"latte") == md5_of(b"latte")
    assert len(md5_of(b"latte")) == 16
    assert md5_of(b"latte") != md5_of(b"mocha")


def test_stale_documents_are_the_ones_no_longer_in_the_catalogue():
    existing = [
        {"id": "latte", "category": "Coffee"},
        {"id": "dark-chocolate-packaged", "category": "Packaged Chocolate"},
        {"id": "croissant", "category": "Bakery"},
    ]
    stale = stale_documents(existing, keep={"latte", "croissant"})
    assert stale == [{"id": "dark-chocolate-packaged", "category": "Packaged Chocolate"}]


def test_nothing_is_stale_when_the_catalogue_matches():
    assert stale_documents([{"id": "latte", "category": "Coffee"}], keep={"latte"}) == []


def test_every_catalogue_image_exists_on_disk():
    from src.data_pipelines.catalog import RAW_PATH, build_catalog, read_jsonl
    from src.data_pipelines.ingest_catalog import IMAGES_DIR

    missing = [
        p.image_file
        for p in build_catalog(read_jsonl(RAW_PATH))
        if not (IMAGES_DIR / p.image_file).is_file()
    ]
    assert missing == []
