"""Helpers for visualizing document_processor models with Erdantic."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from typing import Any

from .models import DocIR


def _load_erdantic():
    try:
        import erdantic as erd
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        if getattr(exc, "name", None) == "erdantic":
            raise RuntimeError(
                "Erdantic is not installed. Install the visualization extra with "
                "`pip install \"document-processor[viz]\"` and ensure Graphviz is available."
            ) from exc
        raise RuntimeError(
            "Erdantic is installed, but one of its native dependencies failed to load. "
            f"Original import error: {exc}. Ensure `pygraphviz` is built against the "
            "currently installed Graphviz libraries."
        ) from exc
    return erd


def resolve_model(model: type | str | None = None) -> type:
    """Resolve a model class from a class object or dotted import path."""
    if model is None:
        return DocIR
    if isinstance(model, type):
        return model
    if ":" in model:
        module_name, attr_name = model.split(":", 1)
    else:
        module_name, _, attr_name = model.rpartition(".")
    if not module_name or not attr_name:
        raise ValueError(
            "Model path must be a full dotted import path like "
            "`document_processor.DocIR` or `pkg.module:Model`."
        )
    module = importlib.import_module(module_name)
    resolved = getattr(module, attr_name)
    if not isinstance(resolved, type):
        raise TypeError(f"Resolved object is not a class: {model}")
    return resolved


def create_model_diagram(model: type | str | None = None):
    """Create an Erdantic diagram object for a model tree."""
    erd = _load_erdantic()
    return erd.create(resolve_model(model))


def draw_model_diagram(
    *,
    out: str | Path,
    model: type | str | None = None,
    graph_attr: dict[str, Any] | None = None,
    node_attr: dict[str, Any] | None = None,
    edge_attr: dict[str, Any] | None = None,
) -> Path:
    """Render an Erdantic diagram for a model tree."""
    erd = _load_erdantic()
    output_path = Path(out)
    erd.draw(
        resolve_model(model),
        out=str(output_path),
        graph_attr=graph_attr,
        node_attr=node_attr,
        edge_attr=edge_attr,
    )
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render an Erdantic diagram for document_processor models.",
    )
    parser.add_argument(
        "--model",
        default="document_processor.DocIR",
        help="Dotted import path or module:class reference. Default: document_processor.DocIR",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output image path. Format is inferred from the file extension.",
    )
    args = parser.parse_args(argv)

    draw_model_diagram(out=args.out, model=args.model)
    return 0


__all__ = [
    "create_model_diagram",
    "draw_model_diagram",
    "main",
    "resolve_model",
]
