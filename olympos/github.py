"""GitHub: one GraphQL query per owner through the `gh` CLI (its login, no token here)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404
from dataclasses import asdict, dataclass, field

QUERY = """
query($owner: String!) {
  repositoryOwner(login: $owner) {
    repositories(first: 100, ownerAffiliations: OWNER, orderBy: {field: PUSHED_AT, direction: DESC}) {
      nodes {
        name visibility isArchived pushedAt url
        defaultBranchRef { name target { ... on Commit { statusCheckRollup { state
          contexts(first: 50) { nodes { __typename
            ... on CheckRun { name conclusion status }
            ... on StatusContext { context state } } } } } } }
        pullRequests(states: OPEN) { totalCount }
        issues(states: OPEN) { totalCount }
      }
    }
  }
}
"""


@dataclass
class Repo:
    owner: str
    name: str
    visibility: str = ""       # PUBLIC / PRIVATE / INTERNAL
    archived: bool = False
    pushed_at: str = ""
    url: str = ""
    ci: str = ""               # SUCCESS / FAILURE / ERROR / PENDING / EXPECTED / "" (no checks)
    open_prs: int = 0
    open_issues: int = 0
    checks: int = 0                                   # jobs in the latest run on the default branch
    failed_checks: list = field(default_factory=list)  # names of the ones that failed

    @property
    def full(self) -> str:
        return f"{self.owner}/{self.name}".lower()

    def to_dict(self) -> dict:
        return asdict(self)


def parse(owner: str, data: dict) -> list[Repo]:
    nodes = (((data.get("data") or {}).get("repositoryOwner") or {}).get("repositories") or {}).get("nodes") or []
    out = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("name"):
            continue
        target = ((n.get("defaultBranchRef") or {}).get("target") or {})
        rollup = target.get("statusCheckRollup") or {}
        nodes_ = ((rollup.get("contexts") or {}).get("nodes") or [])
        failed = []
        for c in nodes_:
            if not isinstance(c, dict):
                continue
            state = c.get("conclusion") or c.get("state") or ""
            if state in ("FAILURE", "ERROR", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"):
                failed.append(str(c.get("name") or c.get("context") or "?")[:80])
        out.append(Repo(
            owner=owner, name=str(n["name"]), visibility=str(n.get("visibility") or ""),
            archived=bool(n.get("isArchived")), pushed_at=str(n.get("pushedAt") or ""), url=str(n.get("url") or ""),
            ci=str(rollup.get("state") or ""), open_prs=int((n.get("pullRequests") or {}).get("totalCount") or 0),
            open_issues=int((n.get("issues") or {}).get("totalCount") or 0),
            checks=sum(1 for c in nodes_ if isinstance(c, dict)), failed_checks=sorted(set(failed)),
        ))
    return out


OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def fetch(owner: str, timeout: float = 30) -> list[Repo]:
    """Raises RuntimeError with a short reason when gh is missing, logged out or offline."""
    if not OWNER.match(owner):
        # The owner comes from a repository's remote URL. Never pass anything else to gh.
        raise RuntimeError(f"{owner!r} is not a GitHub user or organisation name")
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("the gh CLI is not installed")
    out = subprocess.run([gh, "api", "graphql", "-f", f"query={QUERY}", "-f", f"owner={owner}"],  # nosec B603
                         capture_output=True, text=True, timeout=timeout, check=False)
    if out.returncode != 0:
        lines = out.stderr.strip().splitlines()
        raise RuntimeError(lines[-1][:200] if lines else "gh failed")
    try:
        return parse(owner, json.loads(out.stdout))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"unexpected answer from GitHub: {exc}") from None
