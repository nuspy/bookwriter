"""bookgen — compose a book from markdown sources and export it.

Public entry points:

    from bookgen import load_config, build_book
    from bookgen.pdf import build_pdf
    from bookgen.docx_out import build_docx
    from bookgen.epub import build_epub
    from bookgen.cover import build_covers
    from bookgen.check import check_all

or from a shell, which is what non-Claude agents should use:

    python -m bookgen build /path/to/project
"""

__version__ = "1.0.0"

from .assemble import build_book, stats                          # noqa: F401
from .config import Config, from_mapping, load as load_config    # noqa: F401
