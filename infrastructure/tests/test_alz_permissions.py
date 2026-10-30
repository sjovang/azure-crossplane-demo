import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


INFRASTRUCTURE = Path(__file__).resolve().parents[1]
SCOPE = (
    "/providers/Microsoft.Management/managementGroups/"
    "11111111-1111-4111-8111-111111111111"
)
ACTIONS = [
    "Microsoft.Management/managementGroups/read",
    "Microsoft.Management/managementGroups/write",
    "Microsoft.Management/managementGroups/delete",
]


class AlzPermissionTests(unittest.TestCase):
    def run_helper(self, **env):
        role = {
            "name": "22222222-2222-4222-8222-222222222222",
            "roleName": "azure-crossplane-demo ALZ Management Groups",
            "roleType": "CustomRole", "assignableScopes": [SCOPE],
            "permissions": [{
                "actions": ACTIONS, "notActions": [],
                "dataActions": [], "notDataActions": [],
            }],
        }
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)
            credentials = path / "credentials.json"
            credentials.write_text(json.dumps({
                "clientId": "client-id",
                "tenantId": "11111111-1111-4111-8111-111111111111",
                "subscriptionId": "33333333-3333-4333-8333-333333333333",
            }))
            calls = path / "calls"
            definition = path / "definition"
            calls.touch()
            script = r"""
set -euo pipefail
source "$COMMON"
az() {
  printf '%s\n' "$*" >> "$CALLS"
  case "$*" in
    "role definition list"*)
      [[ "${READ_FAILURE:-0}" == 0 ]] || { echo "Read forbidden" >&2; return 1; }
      if [[ "${ROLE_EXISTS:-1}" == 1 ]]; then
        jq -n --argjson role "$ROLE" '[$role]'
      else
        echo '[]'
      fi
      ;;
    "role definition create"*)
      [[ "${CREATE_FAILURE:-0}" == 0 ]] || { echo "Create forbidden" >&2; return 1; }
      while [[ "$1" != "--role-definition" ]]; do shift; done
      printf '%s' "$2" > "$DEFINITION"
      echo "$ROLE"
      ;;
    "ad sp show"*) echo principal-object-id ;;
    "role assignment list"*)
      [[ "${ASSIGNMENT_READ_FAILURE:-0}" == 0 ]] || return 1
      echo "${ASSIGNMENT-assignment-id}"
      ;;
    "role assignment create"*)
      [[ "${ASSIGNMENT_FAILURE:-0}" == 0 ]] || { echo "Assignment forbidden" >&2; return 1; }
      ;;
    *) echo "Unexpected Azure command: $*" >&2; return 1 ;;
  esac
}
ensure_alz_management_group_permissions "$CREDENTIALS"
"""
            result = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ, "COMMON": str(INFRASTRUCTURE / "lib/common.sh"),
                    "CREDENTIALS": str(credentials), "CALLS": str(calls),
                    "DEFINITION": str(definition), "ROLE": json.dumps(role), **env,
                },
                text=True, capture_output=True,
            )
            return (
                result, calls.read_text(),
                json.loads(definition.read_text()) if definition.exists() else None,
            )

    def test_existing_role_and_assignment_are_reused_without_writes(self):
        result, calls, _ = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("role definition create", calls)
        self.assertNotIn("role assignment create", calls)
        self.assertIn(f"--scope {SCOPE}", calls)

    def test_new_role_grants_only_management_group_lifecycle(self):
        result, calls, definition = self.run_helper(ROLE_EXISTS="0", ASSIGNMENT="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(definition["Actions"], ACTIONS)
        self.assertEqual(definition["AssignableScopes"], [SCOPE])
        for field in ("NotActions", "DataActions", "NotDataActions"):
            self.assertEqual(definition[field], [])
        self.assertIn("role assignment create --assignee-object-id principal-object-id", calls)
        self.assertIn("--assignee-principal-type ServicePrincipal", calls)
        self.assertNotIn("--role Owner", calls)
        self.assertNotIn("--role Contributor", calls)

    def test_reused_principal_gets_missing_assignment(self):
        result, calls, _ = self.run_helper(ASSIGNMENT="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("role definition create", calls)
        self.assertIn("role assignment create", calls)

    def test_overprivileged_existing_role_is_not_assigned_or_modified(self):
        role = {
            "name": "other", "roleType": "CustomRole",
            "roleName": "azure-crossplane-demo ALZ Management Groups",
            "assignableScopes": [SCOPE],
            "permissions": [{
                "actions": ["*"], "notActions": [],
                "dataActions": [], "notDataActions": [],
            }],
        }
        result, calls, _ = self.run_helper(ROLE=json.dumps(role), ASSIGNMENT="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("refusing assignment", result.stderr)
        self.assertNotIn("role assignment create", calls)
        self.assertNotIn("role definition create", calls)
        self.assertNotIn("role definition update", calls)

    def test_role_read_failure_does_not_trigger_writes(self):
        result, calls, _ = self.run_helper(READ_FAILURE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: cannot read", result.stderr)
        self.assertNotIn("role definition create", calls)
        self.assertNotIn("role assignment create", calls)

    def test_insufficient_admin_rights_warn_without_stopping_bootstrap(self):
        for env, message in (
            ({"ROLE_EXISTS": "0", "CREATE_FAILURE": "1"}, "roleDefinitions/write"),
            ({"ASSIGNMENT": "", "ASSIGNMENT_FAILURE": "1"}, "roleAssignments/write"),
        ):
            with self.subTest(env=env):
                result, _, _ = self.run_helper(**env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("WARNING:", result.stderr)
                self.assertIn(message, result.stderr)
                self.assertIn(SCOPE, result.stderr)

    def test_assignment_read_failure_is_not_treated_as_missing(self):
        result, calls, _ = self.run_helper(ASSIGNMENT_READ_FAILURE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("refusing to create a duplicate", result.stderr)
        self.assertNotIn("role assignment create", calls)

    def test_both_bootstraps_upgrade_reused_principals(self):
        for name in ("bootstrap-kiac.sh", "bootstrap-aks.sh"):
            text = (INFRASTRUCTURE / name).read_text()
            self.assertIn('ensure_alz_management_group_permissions "$credentials_file"', text)
            self.assertLess(
                text.index('ensure_service_principal "$credentials_file"'),
                text.index('ensure_alz_management_group_permissions "$credentials_file"'),
            )


if __name__ == "__main__":
    unittest.main()
