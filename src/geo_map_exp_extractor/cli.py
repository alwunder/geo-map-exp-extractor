"""Typer command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console

from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.exporters import (
    build_manifest,
    sidecar_paths,
    write_csv,
    write_json,
    write_manifest,
    write_prompt,
)
from geo_map_exp_extractor.image_io import get_image_metadata
from geo_map_exp_extractor.openai_runner import DEFAULT_MODEL, run_extraction
from geo_map_exp_extractor.prompt_builder import build_prompt

app = typer.Typer(
    help="Extract structured tables from geologic map explanation images.",
    invoke_without_command=True,
)
console = Console()


@app.callback(invoke_without_command=True)
def main(
    image: Annotated[Path, typer.Option("--image", help="Input explanation panel image.")],
    profile: Annotated[Path, typer.Option("--profile", help="YAML extraction profile.")],
    out: Annotated[Path, typer.Option("--out", help="Output CSV path.")],
    prompt_template: Annotated[
        Path,
        typer.Option("--prompt-template", help="Markdown extraction prompt template."),
    ] = Path("prompts/extraction_prompt.md"),
    model: Annotated[str, typer.Option("--model", help="OpenAI model name.")] = DEFAULT_MODEL,
    env_file: Annotated[
        Path | None,
        typer.Option(
            "--env-file", help="Optional .env file to load before reading OPENAI_API_KEY."
        ),
    ] = None,
) -> None:
    """Run one image extraction and write CSV plus sidecar files."""

    if env_file is not None:
        load_dotenv(env_file)
    else:
        load_dotenv()

    extraction_profile = load_profile(profile)
    prompt = build_prompt(extraction_profile, prompt_template)
    result = run_extraction(
        image_path=image,
        prompt=prompt,
        profile=extraction_profile,
        model=model,
    )

    paths = sidecar_paths(out)
    write_csv(result.data["rows"], extraction_profile.fields, paths["csv"])
    write_json(result.raw_response, paths["raw_json"])
    write_prompt(prompt, paths["prompt"])
    manifest = build_manifest(
        image_metadata=get_image_metadata(image),
        profile=extraction_profile,
        profile_path=profile,
        model=model,
        output_paths=paths,
    )
    write_manifest(manifest, paths["manifest"])

    console.print(f"[green]Wrote[/green] {paths['csv']}")
    console.print(f"[green]Wrote[/green] {paths['raw_json']}")
    console.print(f"[green]Wrote[/green] {paths['prompt']}")
    console.print(f"[green]Wrote[/green] {paths['manifest']}")


if __name__ == "__main__":
    app()
