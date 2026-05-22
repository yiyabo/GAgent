from io import BytesIO

from fastapi import UploadFile

from app.routers import upload_routes


def test_upload_accepts_single_cell_and_multimodal_formats() -> None:
    for filename in [
        "pbmc.h5ad",
        "atlas.loom",
        "multiome.h5mu",
        "seurat.h5seurat",
        "seurat_object.rds",
        "matrix.mtx",
        "matrix.mtx.gz",
        "sample.barcodes.tsv.gz",
        "sample.features.tsv.gz",
        "sample.genes.tsv.gz",
    ]:
        assert upload_routes.get_upload_file_category("application/octet-stream", filename) == "bioinformatics"


def test_upload_accepts_common_index_browser_and_omics_formats() -> None:
    for filename in [
        "alignments.cram",
        "alignments.crai",
        "alignments.bai",
        "variants.bcf",
        "variants.bcf.gz",
        "variants.tbi",
        "variants.csi",
        "signal.bw",
        "signal.bigwig",
        "regions.bb",
        "regions.bigbed",
        "coverage.bedgraph.gz",
        "taxonomy.biom",
        "qiime.qza",
        "qiime.qzv",
        "proteome.mzML",
        "spectra.mgf",
        "structure.mmcif",
    ]:
        assert upload_routes.get_upload_file_category("application/octet-stream", filename) == "bioinformatics"


def test_upload_prefers_specific_bioinformatics_suffix_over_generic_archive_suffix() -> None:
    assert upload_routes.get_upload_file_category("application/gzip", "matrix.mtx.gz") == "bioinformatics"
    assert upload_routes.get_upload_file_category("application/gzip", "variants.bcf.gz") == "bioinformatics"
    assert upload_routes.get_upload_file_category("application/gzip", "sample.features.tsv.gz") == "bioinformatics"


def test_validate_file_accepts_h5ad_with_empty_browser_mime_type() -> None:
    file = UploadFile(BytesIO(b""), filename="dataset.h5ad")

    is_valid, error, category = upload_routes.validate_upload_file(file)

    assert is_valid is True
    assert error == ""
    assert category == "bioinformatics"
