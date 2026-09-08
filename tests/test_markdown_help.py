from pathlib import Path

from geo_map_exp_extractor.markdown_help import (
    parse_markdown_image,
    parse_markdown_link,
    resolve_markdown_image_path,
)


def test_parses_standard_markdown_image() -> None:
    assert parse_markdown_image("![Workflow diagram](screenshots/workflow.png)") == (
        "Workflow diagram",
        "screenshots/workflow.png",
    )


def test_parses_angle_bracket_image_target() -> None:
    assert parse_markdown_image("![A diagram](<screenshots/a diagram.png>)") == (
        "A diagram",
        "screenshots/a diagram.png",
    )


def test_parses_markdown_link() -> None:
    assert parse_markdown_link("[OpenAI documentation](https://developers.openai.com)") == (
        "OpenAI documentation",
        "https://developers.openai.com",
    )


def test_resolves_local_image_relative_to_readme() -> None:
    root = Path(__file__).resolve().parents[1]
    resolved = resolve_markdown_image_path(
        "screenshots/Vision-basedExtractionOfDMUsFigure.png",
        root,
    )
    assert resolved == (root / "screenshots" / "Vision-basedExtractionOfDMUsFigure.png").resolve()
    assert resolved.is_file()


def test_does_not_resolve_remote_image_as_local_file() -> None:
    assert resolve_markdown_image_path("https://example.com/workflow.png", Path.cwd()) is None
