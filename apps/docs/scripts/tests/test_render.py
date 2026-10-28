import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrdoc.load import CompositionDoc, Sidecar
from xrdoc.render import render_index


def composition(vendor, kind, summary=""):
    return CompositionDoc(
        directory=Path("compositions/apis") / vendor / kind.lower(),
        xrd={"spec": {"names": {"kind": kind}}},
        composition=None,
        sidecar=Sidecar(summary=summary),
        root=Path("."),
    )


class RenderIndexTests(unittest.TestCase):
    def test_groups_compositions_under_sidebar_vendor_categories(self):
        docs = [
            composition("azure", "XAppService", "A web app."),
            composition("azure", "XDatabase", "A database."),
            composition("entraid", "XSecurityGroup", "A security group."),
            composition("platform", "XWebApplication", "An application."),
        ]

        result = render_index(docs)
        sections = result.split("\n## ")

        self.assertEqual(len(sections), 4)
        for section, vendor, expected_docs in zip(
            sections[1:],
            ["Azure", "Entraid", "Platform"],
            [docs[:2], docs[2:3], docs[3:]],
        ):
            self.assertTrue(section.startswith(vendor + "\n\n"))
            self.assertEqual(section.count("| Composition | Summary |"), 1)
            for doc in expected_docs:
                row = "| [{0}]({1}.md) | {2} |".format(
                    doc.kind, doc.slug, doc.sidecar.summary
                )
                self.assertIn(row, section)
                self.assertEqual(result.count(row), 1)
            positions = [section.index(doc.kind) for doc in expected_docs]
            self.assertEqual(positions, sorted(positions))
        self.assertNotIn("| Vendor |", result)
        self.assertNotIn("hide:", result)

    def test_flattens_multiline_summaries_and_preserves_missing_summary(self):
        result = render_index(
            [
                composition("azure", "XAppService", "A web app.\nWith a plan."),
                composition("azure", "XDatabase"),
            ]
        )

        self.assertIn("A web app. With a plan.", result)
        self.assertIn("| [XDatabase](xdatabase.md) | \u2014 |", result)
        self.assertEqual(result.count("## Azure"), 1)

    def test_empty_catalog_has_no_empty_category_or_table(self):
        result = render_index([])

        self.assertIn("# Composition reference", result)
        self.assertNotIn("\n## ", result)
        self.assertNotIn("| Composition |", result)


if __name__ == "__main__":
    unittest.main()
