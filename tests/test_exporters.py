import csv
import json
from pathlib import Path

from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.exporters import build_manifest, sidecar_paths, write_csv, write_manifest
from geo_map_exp_extractor.image_io import ImageMetadata


def test_write_csv_preserves_field_order_and_blanks_missing_values(tmp_path: Path) -> None:
    fields = ["MapUnit", "Description", "Notes"]
    rows = [
        {"Description": "Alluvium", "MapUnit": "Qa", "Extra": "ignored"},
        {"MapUnit": "Tb", "Notes": "partly illegible"},
    ]
    output = tmp_path / "rows.csv"

    write_csv(rows, fields, output)

    with output.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == fields
        loaded = list(reader)

    assert loaded == [
        {"MapUnit": "Qa", "Description": "Alluvium", "Notes": ""},
        {"MapUnit": "Tb", "Description": "", "Notes": "partly illegible"},
    ]


def test_build_and_write_manifest(tmp_path: Path) -> None:
    profile = load_profile(Path("profiles/water_production.yml"))
    output_paths = sidecar_paths(tmp_path / "water.csv")
    manifest = build_manifest(
        image_metadata=ImageMetadata(
            path=Path("input/water.png"),
            width=1200,
            height=800,
            mime_type="image/png",
        ),
        profile=profile,
        profile_path=Path("profiles/water_production.yml"),
        model="gpt-4o-mini",
        output_paths=output_paths,
        timestamp="2026-05-06T00:00:00+00:00",
    )

    manifest_path = tmp_path / "water.manifest.json"
    write_manifest(manifest, manifest_path)
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert loaded["input_image_path"] == "input/water.png"
    assert loaded["input_image_dimensions"] == {"width": 1200, "height": 800}
    assert loaded["profile_id"] == "water_production"
    assert loaded["model"] == "gpt-4o-mini"
    assert loaded["output_file_paths"]["csv"].endswith("water.csv")
    assert "package_version" in loaded
