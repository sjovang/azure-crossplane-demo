import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


COMMON = Path(__file__).resolve().parents[1] / "lib/common.sh"
ROLES = [
    "1bfefb4e-e0b5-418b-a88f-73c46d2cc8e9",
    "9acd699f-1e81-4958-b001-93b1d2506e19",
    "62a82d76-70ea-41e2-9197-370581804d09",
    "df021288-bdef-4463-88db-98f22de89214",
]


class GraphPermissionTests(unittest.TestCase):
    def run_helper(self, helper, grants, **env):
        with tempfile.TemporaryDirectory() as work:
            credentials = Path(work) / "credentials.json"
            credentials.write_text(json.dumps({"clientId": "client-id"}))
            script = r"""
set -euo pipefail
source "$COMMON"
az() {
  printf '%s\n' "$*" >> "$CALLS"
  case "$*" in
    "ad sp show --id 00000003-0000-0000-c000-000000000000"*) echo graph-sp ;;
    "ad sp show"*) echo client-sp ;;
    "ad app show"*) echo configured ;;
    "rest --method GET"*)
      if [[ "${READ_FAILURE:-0}" == 1 ]]; then
        echo "Graph read forbidden" >&2
        return 1
      fi
      if [[ "$*" == *"--query"* ]]; then
        echo "${ASSIGNED-assigned}"
      else
        echo "$GRANTS"
      fi
      ;;
    "rest --method POST"*) echo "Admin consent denied" >&2; return 1 ;;
    *) echo "Unexpected Azure write: $*" >&2; return 1 ;;
  esac
}
"$HELPER" "$CREDENTIALS"
"""
            calls = Path(work) / "calls"
            result = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ, "COMMON": str(COMMON), "CALLS": str(calls),
                    "CREDENTIALS": str(credentials), "HELPER": helper,
                    "GRANTS": json.dumps({"value": grants}), **env,
                },
                text=True, capture_output=True,
            )
            return result, calls.read_text()

    def grants(self, resource="graph-sp"):
        return [{"resourceId": resource, "appRoleId": role} for role in ROLES]

    def test_existing_grants_are_verified_without_writes(self):
        for helper in ("verify_graph_permissions", "ensure_graph_permissions"):
            result, calls = self.run_helper(helper, self.grants())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertNotIn("--method POST", calls)
            self.assertNotIn("permission add", calls)

    def test_declared_but_unconsented_entitlement_permission_fails_audit(self):
        grants = [grant for grant in self.grants() if grant["appRoleId"] != ROLES[1]]
        result, calls = self.run_helper("verify_graph_permissions", grants)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EntitlementManagement.ReadWrite.All", result.stderr)
        self.assertNotIn("--method POST", calls)

    def test_roles_on_another_resource_do_not_count_as_graph_consent(self):
        result, _ = self.run_helper("verify_graph_permissions", self.grants("other"))
        self.assertNotEqual(result.returncode, 0)
        for name in ("Application", "EntitlementManagement", "Group", "User"):
            self.assertIn(f"{name}.Read", result.stderr)

    def test_consent_failure_warns_and_preserves_bootstrap_continue_behavior(self):
        result, calls = self.run_helper(
            "ensure_graph_permissions", [], ASSIGNED=""
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--method POST", calls)
        self.assertIn("Admin consent denied", result.stderr)
        self.assertIn("EntitlementManagement.ReadWrite.All", result.stderr)
        self.assertIn("az ad app permission admin-consent", result.stderr)

    def test_graph_read_failure_is_not_treated_as_missing_consent(self):
        result, calls = self.run_helper(
            "ensure_graph_permissions", [], READ_FAILURE="1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Graph read forbidden", result.stderr)
        self.assertIn("WARNING: could not grant", result.stderr)
        self.assertNotIn("--method POST", calls)


if __name__ == "__main__":
    unittest.main()
