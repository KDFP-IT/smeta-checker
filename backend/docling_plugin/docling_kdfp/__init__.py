"""Docling engine adapter; physical extraction remains in smeta-checker's core."""
from contextlib import contextmanager
from functools import lru_cache
import json
import os
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile
from typing import ClassVar, Literal

import pymupdf
from docling.datamodel.base_models import Cluster, LayoutPrediction, Table, TableStructurePrediction
from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.pipeline_options import BaseTableStructureOptions, TableFormerMode, TableStructureOptions
from docling.models.base_table_model import BaseTableStructureModel
from docling.models.stages.table_structure.table_structure_model import TableStructureModel
from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel

from pdf_table_export import evidence_document, prepare_table_pages, source_identity, table_data, visible_box


def _table_clusters(page):
    layout = page.predictions.layout
    return [cluster for cluster in (layout.clusters if layout else [])
            if cluster.label == DocItemLabel.TABLE]


def _has_native_text(page, pdf_page, cluster):
    box = cluster.bbox
    height = page.size.height if page.size is not None else pdf_page.rect.height
    if getattr(box, "coord_origin", None) == CoordOrigin.BOTTOMLEFT:
        rect = (box.l, height - box.b, box.r, height - box.t)
    else:
        rect = (box.l, box.t, box.r, box.b)
    clip = pymupdf.Rect(rect)
    return any(
        pymupdf.Rect(word[:4]).intersects(clip) and any(ch.isalpha() for ch in word[4])
        for word in pdf_page.get_text("words")
    )


def _split_pages(path, pages):
    """Native tables go to TableFormer; raster tables go to KDFP."""
    native, raster = [], []
    seen = set()
    with pymupdf.open(path) as document:
        for page in pages:
            if page.page_no in seen:
                continue
            seen.add(page.page_no)
            clusters = _table_clusters(page)
            if not clusters:
                continue
            pdf_page = document[page.page_no - 1]
            if all(_has_native_text(page, pdf_page, cluster) for cluster in clusters):
                native.append(page)
            else:
                raster.append(page)
    return raster, native


@lru_cache(maxsize=1)
def _tableformer():
    base = Path(os.environ.get("DOCLING_SERVE_ARTIFACTS_PATH", "/models"))
    artifacts = base / "docling-project--docling-models" / "model_artifacts" / "tableformer"
    return TableStructureModel(
        enabled=True,
        artifacts_path=artifacts,
        options=TableStructureOptions(mode=TableFormerMode.ACCURATE, do_cell_matching=True),
        accelerator_options=AcceleratorOptions(),
    )


class KdfpTableOptions(BaseTableStructureOptions):
    kind: ClassVar[Literal["kdfp"]] = "kdfp"
    cache_dir: str = ".audit_cache/table-extraction"


@contextmanager
def _original_pdf(conv_res, directory):
    backend = conv_res.input._backend
    source = backend.path_or_stream if backend is not None else None
    if isinstance(source, (str, Path)):
        yield Path(source)
        return
    if source is None or not hasattr(source, "read") or not hasattr(source, "seek"):
        raise ValueError("KDFP requires the original PDF path or seekable input stream")
    directory.mkdir(parents=True, exist_ok=True)
    position = source.tell()
    temporary = None
    try:
        with NamedTemporaryFile(dir=directory, suffix=".pdf", delete=False) as stream:
            temporary = Path(stream.name)
            source.seek(0)
            shutil.copyfileobj(source, stream, length=1024*1024)
        source.seek(position)
        yield temporary
    finally:
        source.seek(position)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class KdfpTableStructureModel(BaseTableStructureModel):
    def __init__(self, enabled=True, options=None, **_):
        self.enabled = enabled
        self.options = options or KdfpTableOptions()

    @classmethod
    def get_options_type(cls):
        return KdfpTableOptions

    def predict_tables(self, conv_res, pages):
        if not pages:
            return []
        directory = Path(self.options.cache_dir)
        with _original_pdf(conv_res, directory) as path:
            source = source_identity(path)
            source["filename"] = conv_res.input.file.name
            targets, native_pages = _split_pages(path, pages)
            records = (list(prepare_table_pages(path, [int(page.page_no) for page in targets], cache_dir=directory))
                       if targets else [])
        by_page = {record["page"]: record for record in records}
        predictions = {}
        for page in targets:
            record = by_page[page.page_no]
            # The standard Docling table schema cannot hold independent OCR proofs.
            # Keep the full canonical evidence alongside its normal extraction cache.
            destination = directory/source["sha256"]/"evidence"/f"page-{page.page_no:05d}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(evidence_document([record], source), ensure_ascii=False), encoding="utf-8")
            temporary.replace(destination)
            fatal = [error for error in record["errors"]
                     if error != "incomplete_cell_observation"]
            if fatal:
                raise RuntimeError(f"KDFP page {page.page_no} preparation failed: {fatal}")
            geometry = record["geometry"]
            if page.size is None or abs(page.size.width-geometry["width"]) > .1 or abs(
                    page.size.height-geometry["height"]) > .1:
                raise ValueError("Docling and original PDF displayed page geometry differ")
            layout = page.predictions.layout or LayoutPrediction()
            clusters = list(layout.clusters)
            next_id = max((cluster.id for cluster in clusters), default=-1)+1
            prediction = TableStructurePrediction()
            for table in record["tables"]:
                box = visible_box(table.get("bbox"), geometry)
                if box is None:
                    raise ValueError("KDFP table has no locatable original PDF geometry")
                bbox = BoundingBox(l=box[0], t=box[1], r=box[2], b=box[3], coord_origin=CoordOrigin.TOPLEFT)
                children = [c for c in clusters if c.bbox.intersection_over_self(bbox) > .8]
                held = {c.id for c in children}
                clusters = [c for c in clusters if c.id not in held]
                cluster = Cluster(id=next_id, label=DocItemLabel.TABLE, bbox=bbox,
                                  confidence=0.0, cells=[], children=children)
                clusters.append(cluster)
                data = table_data(table, geometry)
                prediction.table_map[next_id] = Table(id=next_id, label=DocItemLabel.TABLE,
                    page_no=page.page_no, cluster=cluster, otsl_seq=[], num_rows=data.num_rows,
                    num_cols=data.num_cols, table_cells=data.table_cells)
                next_id += 1
            layout.clusters = clusters
            page.predictions.layout = layout
            predictions[page.page_no] = prediction
        if native_pages:
            tableformer = _tableformer()
            for page, prediction in zip(
                    native_pages, tableformer.predict_tables(conv_res, native_pages), strict=True):
                predictions[page.page_no] = prediction
        return [predictions.get(page.page_no, TableStructurePrediction()) for page in pages]


def table_structure_engines():
    return {"table_structure_engines": [KdfpTableStructureModel]}
