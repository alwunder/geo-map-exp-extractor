"""Typer command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from geo_map_exp_extractor.env_utils import load_env_from_candidates
from geo_map_exp_extractor.jobs import ExtractionJobResult, run_extraction_job
from geo_map_exp_extractor.openai_runner import DEFAULT_MODEL
from geo_map_exp_extractor.settings import DEFAULT_IMAGE_DETAIL, DEFAULT_MAX_IMAGE_SIDE_PX

app = typer.Typer(help="Extract structured tables from geologic map explanation images.")
console = Console()


def _load_env(env_file: Path | None) -> None:
    repo_env = Path(__file__).resolve().parents[2] / ".env"
    candidates: list[Path] = [Path.cwd() / ".env", repo_env]
    if env_file is not None:
        candidates.insert(0, env_file)
    load_env_from_candidates(candidates)


def _print_charge_notice(image_count: int) -> None:
    noun = "image" if image_count == 1 else "images"
    console.print(
        f"[yellow]This operation will send {image_count} {noun} and prompts to the OpenAI API. "
        "This may incur API charges.[/yellow]"
    )


def _print_result_summary(job: ExtractionJobResult) -> None:
    console.print(f"[green]Run folder:[/green] {job.run_dir}")
    if job.dry_run:
        console.print(
            "[cyan]Dry run only:[/cyan] no API call made. "
            f"Rough image tokens estimate: {job.rough_image_tokens}"
        )
        return
    usage = job.usage
    console.print(
        "Usage tokens: "
        f"input={usage.get('input_tokens')} "
        f"output={usage.get('output_tokens')} "
        f"total={usage.get('total_tokens')} "
        f"cached={usage.get('cached_tokens')}"
    )
    if job.estimated_cost_usd is None:
        console.print(
            "Estimated cost: unavailable (configure pricing in "
            "src/geo_map_exp_extractor/pricing.py)."
        )
    else:
        console.print(f"Estimated cost (USD): {job.estimated_cost_usd}")
    console.print("API mode: cache reuse" if job.cache_reused else "API mode: fresh call")


@app.command("single")
def single_run(
    image: Annotated[Path, typer.Option("--image", help="Input explanation panel image.")],
    profile: Annotated[Path, typer.Option("--profile", help="YAML extraction profile.")],
    out_dir: Annotated[
        Path,
        typer.Option("--out-dir", help="Directory where timestamped run folders are written."),
    ] = Path("outputs"),
    prompt_template: Annotated[
        Path,
        typer.Option("--prompt-template", help="Markdown extraction prompt template."),
    ] = Path("prompts/extraction_prompt.md"),
    model: Annotated[str, typer.Option("--model", help="OpenAI model name.")] = DEFAULT_MODEL,
    image_detail: Annotated[
        str,
        typer.Option("--image-detail", help="Image detail level: high, auto, or low."),
    ] = DEFAULT_IMAGE_DETAIL,
    include_profile_notes: Annotated[
        bool,
        typer.Option("--include-profile-notes", help="Append profile notes to the prompt."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Prepare prompt/schema/image and estimate usage without API."),
    ] = False,
    rerun: Annotated[
        bool,
        typer.Option("--rerun", help="Force a fresh API call even if cache fingerprint matches."),
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Disable cache lookup and cache writes."),
    ] = False,
    max_image_side_px: Annotated[
        int,
        typer.Option("--max-image-side-px", help="Resize down only when either side exceeds this."),
    ] = DEFAULT_MAX_IMAGE_SIDE_PX,
    segmented_mode: Annotated[
        bool,
        typer.Option("--segmented-mode", help="Split image into overlapping segments before extraction."),
    ] = False,
    segment_height_px: Annotated[
        int,
        typer.Option("--segment-height-px", help="Segment height in pixels for segmented mode."),
    ] = 1800,
    segment_overlap_px: Annotated[
        int,
        typer.Option("--segment-overlap-px", help="Vertical overlap between segments."),
    ] = 200,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Optional .env file to load before extraction."),
    ] = None,
) -> None:
    """Run one extraction with full audit trail."""

    _load_env(env_file)
    _print_charge_notice(1)
    if segmented_mode:
        console.print("[yellow]Segmented mode enabled: this can increase API cost.[/yellow]")

    result = run_extraction_job(
        image_path=image,
        profile_path=profile,
        output_dir=out_dir,
        model=model,
        image_detail=image_detail,
        prompt_template_path=prompt_template,
        include_profile_notes=include_profile_notes,
        use_cache=not no_cache,
        force_rerun=rerun,
        dry_run=dry_run,
        max_image_side_px=max_image_side_px,
        segmented_mode=segmented_mode,
        segment_height_px=segment_height_px,
        segment_overlap_px=segment_overlap_px,
    )
    _print_result_summary(result)


@app.command("batch")
def batch_run(
    image_dir: Annotated[Path, typer.Option("--image-dir", help="Folder of images for batch mode.")],
    profile: Annotated[Path, typer.Option("--profile", help="YAML extraction profile.")],
    out_dir: Annotated[
        Path,
        typer.Option("--out-dir", help="Directory where timestamped run folders are written."),
    ] = Path("outputs"),
    pattern: Annotated[
        str,
        typer.Option("--pattern", help="Glob for images inside --image-dir."),
    ] = "*.*",
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip explicit confirmation prompt."),
    ] = False,
    prompt_template: Annotated[
        Path,
        typer.Option("--prompt-template", help="Markdown extraction prompt template."),
    ] = Path("prompts/extraction_prompt.md"),
    model: Annotated[str, typer.Option("--model", help="OpenAI model name.")] = DEFAULT_MODEL,
    image_detail: Annotated[
        str,
        typer.Option("--image-detail", help="Image detail level: high, auto, or low."),
    ] = DEFAULT_IMAGE_DETAIL,
    include_profile_notes: Annotated[
        bool,
        typer.Option("--include-profile-notes", help="Append profile notes to the prompt."),
    ] = False,
    rerun: Annotated[
        bool,
        typer.Option("--rerun", help="Force fresh API calls even if cache matches."),
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Disable cache lookup and cache writes."),
    ] = False,
    max_image_side_px: Annotated[
        int,
        typer.Option("--max-image-side-px", help="Resize down only when either side exceeds this."),
    ] = DEFAULT_MAX_IMAGE_SIDE_PX,
    segmented_mode: Annotated[
        bool,
        typer.Option("--segmented-mode", help="Split each image into overlapping segments."),
    ] = False,
    segment_height_px: Annotated[
        int,
        typer.Option("--segment-height-px", help="Segment height in pixels for segmented mode."),
    ] = 1800,
    segment_overlap_px: Annotated[
        int,
        typer.Option("--segment-overlap-px", help="Vertical overlap between segments."),
    ] = 200,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Optional .env file to load before extraction."),
    ] = None,
) -> None:
    """Run a confirmed batch extraction. Use only after validating single-image workflow."""

    _load_env(env_file)
    candidates = [path for path in sorted(image_dir.glob(pattern)) if path.is_file()]
    if not candidates:
        raise typer.BadParameter(f"No files matched {pattern!r} under {image_dir}.")

    _print_charge_notice(len(candidates))
    if segmented_mode:
        console.print("[yellow]Segmented mode enabled: this can increase API cost.[/yellow]")
    if not yes:
        proceed = typer.confirm(
            f"Process {len(candidates)} image(s) from {image_dir}? This will make API calls."
        )
        if not proceed:
            console.print("Batch cancelled.")
            raise typer.Exit()

    for image in candidates:
        result = run_extraction_job(
            image_path=image,
            profile_path=profile,
            output_dir=out_dir,
            model=model,
            image_detail=image_detail,
            prompt_template_path=prompt_template,
            include_profile_notes=include_profile_notes,
            use_cache=not no_cache,
            force_rerun=rerun,
            dry_run=False,
            max_image_side_px=max_image_side_px,
            segmented_mode=segmented_mode,
            segment_height_px=segment_height_px,
            segment_overlap_px=segment_overlap_px,
        )
        _print_result_summary(result)


@app.callback(invoke_without_command=True)
def default_command(ctx: typer.Context) -> None:
    """Default to the single-image command when no subcommand is supplied."""

    if ctx.invoked_subcommand is None:
        console.print("Use `single` for one image or `batch` for confirmed folder runs.")


if __name__ == "__main__":
    app()
