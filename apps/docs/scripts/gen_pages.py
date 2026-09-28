"""mkdocs-gen-files entry point.

Generates one reference page per Crossplane composition at build time. Nothing
written here is committed to the repository; the pages exist only inside the
MkDocs build.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mkdocs_gen_files  # noqa: E402

from xrdoc.load import load_compositions  # noqa: E402
from xrdoc.render import render_index, render_page  # noqa: E402

REFERENCE_DIR = "reference"

docs = load_compositions()

nav_lines = [
    "* [Home](index.md)",
    "* [Team namespaces](teams.md)",
    "* [Crossplane conventions](conventions.md)",
    "* Reference",
    "    * [Overview]({0}/index.md)".format(REFERENCE_DIR),
]

with mkdocs_gen_files.open("{0}/index.md".format(REFERENCE_DIR), "w") as handle:
    handle.write(render_index(docs))

for doc in docs:
    page_path = "{0}/{1}.md".format(REFERENCE_DIR, doc.slug)

    with mkdocs_gen_files.open(page_path, "w") as handle:
        handle.write(render_page(doc))

    # "Edit this page" points at the XRD the page was generated from.
    mkdocs_gen_files.set_edit_path(
        page_path, Path("..") / ".." / doc.xrd_path
    )

    nav_lines.append("    * [{0}]({1})".format(doc.kind, page_path))

with mkdocs_gen_files.open("SUMMARY.md", "w") as handle:
    handle.write("\n".join(nav_lines) + "\n")

print("xrdoc: generated {0} reference pages".format(len(docs)))
