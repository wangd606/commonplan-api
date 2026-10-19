from app.ingestion.knowledge_types import SourceMaterial
from app.ingestion.types import ParsedDocument, ParsedPage


RENDERER_VERSION = "knowledge-markdown-v1"


def render_material(material: SourceMaterial) -> ParsedDocument:
    """Render database entities deterministically; files still use Docling."""
    parts = [f"# {material.title.strip()}"]
    for heading, value in material.sections:
        normalized = value.strip()
        if normalized:
            parts.extend((f"## {heading}", normalized))
    markdown = "\n\n".join(parts).strip()
    return ParsedDocument(
        parser_name="database-renderer",
        parser_version=RENDERER_VERSION,
        pages=[ParsedPage(
            page_number=1,
            heading_path=material.title.strip(),
            markdown_content=markdown,
            plain_text=markdown,
        )],
    )
