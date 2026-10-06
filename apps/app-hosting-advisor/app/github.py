import base64
from datetime import datetime, timezone
from typing import Any

import httpx
import yaml


class GitHubError(Exception):
    pass


class GitHubOAuth:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    async def exchange_code(self, code: str) -> tuple[str, str]:
        async with httpx.AsyncClient(timeout=30) as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
            )
            if token_response.is_error:
                raise GitHubError("GitHub OAuth token exchange failed.")
            token = token_response.json().get("access_token")
            if not isinstance(token, str) or not token:
                raise GitHubError("GitHub did not return an access token.")

            user_response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if user_response.is_error:
                raise GitHubError("Could not read the GitHub user profile.")
            login = user_response.json().get("login")
            if not isinstance(login, str) or not login:
                raise GitHubError("GitHub did not return a user login.")
            return token, login


class GitHubPublisher:
    def __init__(self, repository: str, token: str) -> None:
        self.repository = repository
        self.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            timeout=30,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def __aenter__(self) -> "GitHubPublisher":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.client.aclose()

    async def _json(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = await self.client.request(method, path, **kwargs)
        if response.is_error:
            detail = response.text[:500]
            raise GitHubError(
                f"GitHub API {method} {path} failed "
                f"({response.status_code}): {detail}"
            )
        value = response.json()
        if not isinstance(value, dict):
            raise GitHubError("GitHub returned an unexpected response.")
        return value

    async def _content(self, path: str, ref: str) -> tuple[str, str]:
        value = await self._json(
            "GET",
            f"/repos/{self.repository}/contents/{path}",
            params={"ref": ref},
        )
        content = base64.b64decode(value["content"]).decode()
        return content, value["sha"]

    async def publish(
        self,
        team_name: str,
        files: dict[str, str],
        reasoning: str,
    ) -> str:
        repository = await self._json("GET", f"/repos/{self.repository}")
        default_branch = repository["default_branch"]

        existing = await self.client.get(
            f"/repos/{self.repository}/contents/teams/{team_name}",
            params={"ref": default_branch},
        )
        if existing.status_code == 200:
            raise GitHubError(f"teams/{team_name} already exists.")
        if existing.status_code != 404:
            raise GitHubError(
                f"Could not check the team folder ({existing.status_code})."
            )

        reference = await self._json(
            "GET",
            f"/repos/{self.repository}/git/ref/heads/{default_branch}",
        )
        branch = (
            f"advisor/{team_name}-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )
        await self._json(
            "POST",
            f"/repos/{self.repository}/git/refs",
            json={
                "ref": f"refs/heads/{branch}",
                "sha": reference["object"]["sha"],
            },
        )

        root_content, root_sha = await self._content(
            "teams/kustomization.yaml",
            default_branch,
        )
        root = yaml.safe_load(root_content)
        resources = root.setdefault("resources", [])
        if team_name not in resources:
            resources.append(team_name)
            resources.sort()
        files["teams/kustomization.yaml"] = (
            "---\n" + yaml.safe_dump(root, sort_keys=False)
        )

        for path, content in files.items():
            body: dict[str, Any] = {
                "message": f"feat: add {team_name} team",
                "content": base64.b64encode(content.encode()).decode(),
                "branch": branch,
            }
            if path == "teams/kustomization.yaml":
                body["sha"] = root_sha
            await self._json(
                "PUT",
                f"/repos/{self.repository}/contents/{path}",
                json=body,
            )

        pull_request = await self._json(
            "POST",
            f"/repos/{self.repository}/pulls",
            json={
                "title": f"feat: add {team_name} team",
                "head": branch,
                "base": default_branch,
                "body": (
                    "Created by the experimental app hosting advisor.\n\n"
                    f"**Recommendation:** {reasoning}"
                ),
            },
        )
        return pull_request["html_url"]
