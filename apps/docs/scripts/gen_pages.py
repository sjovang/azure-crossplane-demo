"""Generate the composition reference pages.

Run this *before* building the site:

    python scripts/gen_pages.py && zensical build

Zensical does not support `mkdocs-gen-files`, so the pages are written into
`docs/` as real files instead of being injected into the build. They are
regenerated from the XRD and Composition manifests on every build and are
gitignored -- `docs/reference/` is build output, not source.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xrdoc.load import load_compositions  # noqa: E402
from xrdoc.render import render_index, render_page  # noqa: E402

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
REFERENCE_DIR = "reference"


def write(relative_path, text):
    path = DOCS_DIR / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main():
    docs = load_compositions()

    # Wipe first so a removed composition cannot leave a stale page behind.
    shutil.rmtree(DOCS_DIR / REFERENCE_DIR, ignore_errors=True)

    nav_lines = [
        "* [Home](index.md)",
        "* [Team namespaces](teams.md)",
        "* [Crossplane conventions](conventions.md)",
        "* Reference",
        "    * [Overview]({0}/index.md)".format(REFERENCE_DIR),
    ]

    write("{0}/index.md".format(REFERENCE_DIR), render_index(docs))

    for doc in docs:
        page_path = "{0}/{1}.md".format(REFERENCE_DIR, doc.slug)
        write(page_path, render_page(doc))
        nav_lines.append("    * [{0}]({1})".format(doc.kind, page_path))

    write("SUMMARY.md", "\n".join(nav_lines) + "\n")

    print("xrdoc: generated {0} reference pages".format(len(docs)))


if __name__ == "__main__":
    main()
