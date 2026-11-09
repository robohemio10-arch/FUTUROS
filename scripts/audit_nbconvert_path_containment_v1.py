"""Check CVE-2026-114140 containment with a synthetic notebook export."""

from __future__ import annotations

import base64
import json
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory


_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNgAAAAAgABSK+kcQAAAABJRU5ErkJggg=="
)
_SENTINEL = b"outside export directory"
_CASES = ("plain_filename", "relative_traversal", "absolute_traversal")


def _exercise_exports(root: Path) -> None:
    import nbformat
    from nbconvert import MarkdownExporter
    from nbconvert.writers import FilesWriter
    from traitlets.config import Config

    exporter = MarkdownExporter(config=Config({"ExecutePreprocessor": {"enabled": False}}))
    expected_bytes = base64.b64decode(_PNG, validate=True)

    for case in _CASES:
        case_root = root / case
        case_root.mkdir()
        export_root = case_root / "export"
        sentinel = case_root / "image.png"
        sentinel.write_bytes(_SENTINEL)
        filename = {
            "plain_filename": sentinel.name,
            "relative_traversal": str(Path("..", "..", sentinel.name)),
            "absolute_traversal": str(sentinel),
        }[case]
        output = nbformat.v4.new_output(
            "display_data", data={"image/png": _PNG}, metadata={"filename": filename}
        )
        cell = nbformat.v4.new_code_cell(
            source="raise RuntimeError('Do not execute fixture')", outputs=[output]
        )
        notebook = nbformat.v4.new_notebook(cells=[cell])
        body, resources = exporter.from_notebook_node(
            notebook, resources={"output_files_dir": "assets"}
        )

        # Check paths before writing so the probe is safe on a vulnerable wheel.
        outputs = resources.get("outputs", {})
        image_path = Path("assets", sentinel.name)
        if outputs != {str(image_path): expected_bytes} or resources.get("output_extension") != ".md":
            raise RuntimeError(f"{case}: exporter did not contain the extracted image under assets")

        FilesWriter(build_directory=str(export_root)).write(body, resources, notebook_name="fixture")
        if (export_root / image_path).read_bytes() != expected_bytes or not (
            export_root / "fixture.md"
        ).is_file():
            raise RuntimeError(f"{case}: expected export files are missing or incorrect")
        if sentinel.read_bytes() != _SENTINEL:
            raise RuntimeError(f"{case}: file outside the export directory changed")


def main() -> int:
    installed_version = None
    try:
        installed_version = version("nbconvert")
        with TemporaryDirectory(prefix="nbconvert-containment-") as directory:
            _exercise_exports(Path(directory).resolve())
        if installed_version != "7.17.2":
            raise RuntimeError("expected the locked nbconvert version 7.17.2")
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "FAIL", "nbconvert_version": installed_version, "error": str(exc)},
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "status": "PASS",
                "nbconvert_version": installed_version,
                "advisory": "CVE-2026-114140",
                "cases": _CASES,
                "notebook_executed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
