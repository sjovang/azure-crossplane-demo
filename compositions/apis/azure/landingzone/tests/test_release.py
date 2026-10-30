"""Release resolution, offline restoration and OCI deployment boundaries."""

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


API = Path(__file__).resolve().parents[1]
PROJECT = API.parents[2]
sys.path.insert(0, str(API))
import package  # noqa: E402
import update_library  # noqa: E402

spec = importlib.util.spec_from_file_location("alz_release_generator", API / "generate.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = json.loads((API / "release.json").read_text())
        self.lock = json.loads((API / "library-lock.json").read_text())
        (self.root / "release.json").write_text(json.dumps(self.config))
        (self.root / "library-lock.json").write_text(json.dumps(self.lock))
        self.build = self.root / ".build"
        self.build.mkdir()

    def test_release_input_resolves_the_selected_tag(self):
        commit = self.lock["sources"]["alz"]["commit"]
        response = json.dumps({"object": {"type": "commit", "sha": commit}}).encode()
        with patch.object(update_library.subprocess, "check_output", return_value=response) as gh:
            self.assertEqual(update_library.resolve_release(self.config["alz"]), commit)
        self.assertIn(
            "/git/ref/tags/platform/alz/" + self.config["alz"], gh.call_args.args[0][-1],
        )

    def test_annotated_release_tag_is_peeled_to_commit(self):
        commit = self.lock["sources"]["alz"]["commit"]
        responses = [
            json.dumps({"object": {"type": "tag", "sha": "a" * 40}}).encode(),
            json.dumps({"object": {"type": "commit", "sha": commit}}).encode(),
        ]
        with patch.object(update_library.subprocess, "check_output", side_effect=responses):
            self.assertEqual(update_library.resolve_release(self.config["alz"]), commit)

    def test_locked_snapshot_is_restored_from_exact_commits(self):
        with (
            patch.object(update_library, "ROOT", self.root),
            patch.object(update_library, "BUILD", self.build),
            patch.object(update_library, "archive", side_effect=RuntimeError("stop after fetch")) as fetch,
        ):
            with self.assertRaisesRegex(RuntimeError, "stop after fetch"):
                update_library.refresh(locked=True)
        fetch.assert_called_once_with(
            update_library.ALZ_REPOSITORY, self.lock["sources"]["alz"]["commit"],
        )

    def test_valid_cached_snapshot_needs_no_network(self):
        (self.build / "library.json").write_bytes((API / ".build/library.json").read_bytes())
        with (
            patch.object(update_library, "ROOT", self.root),
            patch.object(update_library, "BUILD", self.build),
            patch.object(update_library, "archive") as fetch,
            patch.object(update_library, "resolve_release") as resolve,
        ):
            update_library.refresh(locked=True, if_missing=True)
        fetch.assert_not_called()
        resolve.assert_not_called()

    def test_corrupt_cached_snapshot_fails_without_fallback(self):
        (self.build / "library.json").write_text("{}")
        with (
            patch.object(update_library, "ROOT", self.root),
            patch.object(update_library, "BUILD", self.build),
            patch.object(update_library, "archive") as fetch,
        ):
            with self.assertRaisesRegex(ValueError, "reviewed source lock"):
                update_library.refresh(locked=True, if_missing=True)
        fetch.assert_not_called()

    def test_release_change_requires_a_reviewed_lock(self):
        for field, value in (("alz", "2026.11.0"), ("builtinPoliciesCommit", "a" * 40)):
            config = {**self.config, field: value}
            (self.root / "release.json").write_text(json.dumps(config))
            with (
                patch.object(update_library, "ROOT", self.root),
                patch.object(update_library, "BUILD", self.build),
                patch.object(update_library, "archive") as fetch,
            ):
                with self.assertRaisesRegex(ValueError, "review a new lock"):
                    update_library.refresh(locked=True)
            fetch.assert_not_called()

    def test_invalid_release_input_is_rejected(self):
        for field, value in (("alz", "latest"), ("builtinPoliciesCommit", "main")):
            (self.root / "release.json").write_text(json.dumps({**self.config, field: value}))
            with patch.object(update_library, "ROOT", self.root):
                with self.assertRaises(ValueError):
                    update_library.release_config()

    def test_generated_version_comes_from_locked_release(self):
        composition = generator.generate()["composition.yaml"]
        self.assertIn("libraryVersion: " + self.lock["sources"]["alz"]["release"], composition)
        self.assertNotIn("__LIBRARY_VERSION__", composition)

    def test_generator_rejects_source_drift(self):
        lock = copy.deepcopy(self.lock)
        lock["sources"]["alz"]["release"] = "platform/alz/2026.11.0"
        (self.root / "library-lock.json").write_text(json.dumps(lock))
        with (
            patch.object(generator, "ROOT", self.root),
            patch.object(generator, "BUILD", API / ".build"),
        ):
            with self.assertRaisesRegex(ValueError, "reviewed source lock"):
                generator.generate()

    def test_artifact_contains_manifests_not_gitops_pointers(self):
        dist = self.root / "artifact"
        with patch.object(package, "DIST", dist):
            package.package()
        kustomization = yaml.safe_load((dist / "kustomization.yaml").read_text())
        self.assertEqual(kustomization["resources"], ["definition.yaml", "composition.yaml"])
        self.assertEqual(
            {path.name for path in dist.iterdir()},
            set(package.FILES) | {"kustomization.yaml"},
        )
        self.assertFalse((dist / "oci-repository.yaml").exists())
        xrd = yaml.safe_load((dist / "definition.yaml").read_text())
        properties = xrd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]
        for field in ("managementGroups", "subscriptions", "policyDefaults", "providerConfigRef"):
            self.assertIn(field, properties)
        self.assertEqual(xrd["spec"]["scope"], "Namespaced")

    def test_gitops_waits_for_the_oci_xrd_before_applying_teams(self):
        pointers = yaml.safe_load((API / "kustomization.yaml").read_text())
        self.assertEqual(pointers["resources"], ["oci-repository.yaml", "oci-kustomization.yaml"])
        source = yaml.safe_load((API / "oci-repository.yaml").read_text())
        deploy = yaml.safe_load((API / "oci-kustomization.yaml").read_text())
        self.assertEqual(deploy["spec"]["sourceRef"], {
            "kind": "OCIRepository", "name": source["metadata"]["name"],
        })
        self.assertEqual(
            source["spec"]["layerSelector"]["mediaType"],
            "application/vnd.cncf.flux.content.v1.tar+gzip",
        )
        self.assertEqual(
            {dep["name"] for dep in deploy["spec"]["dependsOn"]},
            {"flux-system", "crossplane-functions"},
        )
        self.assertTrue(deploy["spec"]["wait"])
        self.assertIn("Established", deploy["spec"]["healthCheckExprs"][0]["current"])
        teams = yaml.safe_load((PROJECT.parent / "clusters/base/teams-kustomization.yaml").read_text())
        self.assertIn(
            {"name": deploy["metadata"]["name"]}, teams["spec"]["dependsOn"],
        )


if __name__ == "__main__":
    unittest.main()
