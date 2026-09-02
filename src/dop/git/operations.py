"""Git operations for demands."""

from __future__ import annotations

from pathlib import Path

from ..config.schema import WorkspaceConfig
from ..core.errors import ProcessError, ValidationError
from ..core.process import run_command
from .auth.base import GitAuthProvider


def repo_path(workspace: WorkspaceConfig, repo_name: str) -> Path:
    repo_cfg = workspace.repos.get(repo_name)
    if not repo_cfg:
        raise ValidationError(f"Unknown repo: {repo_name}")
    return Path(workspace.root) / repo_cfg.dir


def create_branch(
    repo_name: str,
    branch_name: str,
    workspace: WorkspaceConfig,
    auth: GitAuthProvider,
    *,
    pull_first: bool = False,
    dry_run: bool = False,
    logger=None,
) -> None:
    repo_cfg = workspace.repos.get(repo_name)
    if not repo_cfg:
        raise ValidationError(f"Missing repo config for {repo_name}")
    repo_dir = repo_path(workspace, repo_name)
    base_branch = repo_cfg.base_branch
    if pull_first:
        pull_branch(
            repo_name,
            base_branch,
            workspace=workspace,
            auth=auth,
            dry_run=dry_run,
            logger=logger,
        )
    run_command(["git", "checkout", base_branch], cwd=repo_dir, dry_run=dry_run, logger=logger)
    run_command(
        ["git", "checkout", "-b", branch_name],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )


def current_branch(
    repo_name: str,
    workspace: WorkspaceConfig,
    *,
    dry_run: bool = False,
    logger=None,
) -> str:
    repo_dir = repo_path(workspace, repo_name)
    result = run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )
    return result.stdout.strip() if result.stdout else ""


def list_local_branches(
    repo_name: str,
    workspace: WorkspaceConfig,
    *,
    dry_run: bool = False,
    logger=None,
) -> list[str]:
    repo_dir = repo_path(workspace, repo_name)
    result = run_command(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )
    if not result.stdout:
        return []
    branches = []
    for line in result.stdout.splitlines():
        branch = line.strip()
        if branch:
            branches.append(branch)
    return branches


def get_remote_url(
    repo_name: str,
    workspace: WorkspaceConfig,
    remote_name: str = "origin",
    *,
    dry_run: bool = False,
    logger=None,
) -> str:
    repo_dir = repo_path(workspace, repo_name)
    result = run_command(
        ["git", "remote", "get-url", remote_name],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )
    return result.stdout.strip() if result.stdout else ""


def _run_git_remote(
    args: list[str],
    *,
    repo_dir: Path,
    auth: GitAuthProvider,
    dry_run: bool = False,
    logger=None,
) -> None:
    prefix = auth.git_command_prefix()
    if not prefix or prefix[0] != "git":
        raise ValidationError("git_command_prefix must start with 'git'.")
    cmd = [*prefix, *args]
    env = auth.git_env() if not dry_run else None
    run_command(cmd, cwd=repo_dir, env=env, dry_run=dry_run, logger=logger)


def has_uncommitted_changes(
    repo_name: str,
    *,
    workspace: WorkspaceConfig,
    dry_run: bool = False,
    logger=None,
) -> bool:
    """Check if repo has uncommitted changes (staged, unstaged, or untracked)."""
    repo_dir = repo_path(workspace, repo_name)
    result = run_command(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )
    return bool(result.stdout and result.stdout.strip())


def commit_changes(
    repo_name: str,
    commit_message: str,
    *,
    workspace: WorkspaceConfig,
    dry_run: bool = False,
    logger=None,
) -> None:
    repo_dir = repo_path(workspace, repo_name)
    run_command(["git", "add", "-A"], cwd=repo_dir, dry_run=dry_run, logger=logger)
    run_command(
        ["git", "commit", "-m", commit_message],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )


def push_branch(
    repo_name: str,
    branch_name: str,
    *,
    workspace: WorkspaceConfig,
    auth: GitAuthProvider,
    dry_run: bool = False,
    logger=None,
) -> None:
    repo_dir = repo_path(workspace, repo_name)
    _run_git_remote(
        ["push", "-u", "origin", branch_name],
        repo_dir=repo_dir,
        auth=auth,
        dry_run=dry_run,
        logger=logger,
    )


def pull_branch(
    repo_name: str,
    branch_name: str | None,
    *,
    workspace: WorkspaceConfig,
    auth: GitAuthProvider,
    dry_run: bool = False,
    logger=None,
) -> None:
    repo_dir = repo_path(workspace, repo_name)
    args = ["pull", "--ff-only"]
    if branch_name:
        args.extend(["origin", branch_name])
    _run_git_remote(args, repo_dir=repo_dir, auth=auth, dry_run=dry_run, logger=logger)


def remote_branch_exists(
    repo_name: str,
    branch_name: str,
    *,
    workspace: WorkspaceConfig,
    auth: GitAuthProvider,
    dry_run: bool = False,
    logger=None,
) -> bool:
    """Verifica se a branch existe em origin via ls-remote (sem network local)."""
    repo_dir = repo_path(workspace, repo_name)
    if dry_run:
        return True
    try:
        prefix = auth.git_command_prefix()
        env = auth.git_env()
        result = run_command(
            [*prefix, "ls-remote", "--heads", "origin", branch_name],
            cwd=repo_dir,
            env=env,
            dry_run=False,
            logger=logger,
        )
        return bool(result.stdout and result.stdout.strip())
    except ProcessError:
        return False


def pull_branch_if_exists(
    repo_name: str,
    branch_name: str,
    *,
    workspace: WorkspaceConfig,
    auth: GitAuthProvider,
    dry_run: bool = False,
    logger=None,
) -> bool:
    """Faz checkout + pull --ff-only de branch_name se existir no remote. Retorna True se atualizou."""
    if not remote_branch_exists(
        repo_name, branch_name,
        workspace=workspace, auth=auth, dry_run=dry_run, logger=logger,
    ):
        if logger:
            logger.info(f"  {repo_name}: branch '{branch_name}' não existe no remote — skip.")
        return False
    try:
        checkout_branch(repo_name, branch_name, workspace=workspace, dry_run=dry_run, logger=logger)
    except ProcessError:
        # Branch só existe no remote; cria local rastreando origin/<branch>.
        repo_dir = repo_path(workspace, repo_name)
        run_command(
            ["git", "checkout", "-B", branch_name, f"origin/{branch_name}"],
            cwd=repo_dir, dry_run=dry_run, logger=logger,
        )
    pull_branch(repo_name, branch_name, workspace=workspace, auth=auth, dry_run=dry_run, logger=logger)
    return True


def fetch_origin(
    repo_name: str,
    *,
    workspace: WorkspaceConfig,
    auth: GitAuthProvider,
    dry_run: bool = False,
    logger=None,
) -> None:
    """git fetch origin."""
    repo_dir = repo_path(workspace, repo_name)
    _run_git_remote(
        ["fetch", "origin"],
        repo_dir=repo_dir,
        auth=auth,
        dry_run=dry_run,
        logger=logger,
    )


def checkout_branch(
    repo_name: str,
    branch_name: str,
    *,
    workspace: WorkspaceConfig,
    dry_run: bool = False,
    logger=None,
) -> None:
    """git checkout <branch> (existing branch)."""
    repo_dir = repo_path(workspace, repo_name)
    run_command(
        ["git", "checkout", branch_name],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )


def checkout_new_branch_from_remote(
    repo_name: str,
    branch_name: str,
    remote_base: str,
    *,
    workspace: WorkspaceConfig,
    dry_run: bool = False,
    logger=None,
) -> None:
    """git checkout -B <branch_name> origin/<remote_base>."""
    repo_dir = repo_path(workspace, repo_name)
    run_command(
        ["git", "checkout", "-B", branch_name, f"origin/{remote_base}"],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )


def merge_remote_branch(
    repo_name: str,
    remote_branch: str,
    *,
    workspace: WorkspaceConfig,
    dry_run: bool = False,
    logger=None,
) -> tuple[bool, list[str]]:
    """git merge origin/<remote_branch>. Returns (success, conflict_files)."""
    repo_dir = repo_path(workspace, repo_name)
    try:
        run_command(
            ["git", "merge", f"origin/{remote_branch}"],
            cwd=repo_dir,
            dry_run=dry_run,
            logger=logger,
        )
        return True, []
    except ProcessError:
        conflicts = get_conflict_files(repo_name, workspace=workspace)
        if conflicts:
            return False, conflicts
        raise


def rebase_on_base(
    repo_name: str,
    base_branch: str,
    *,
    workspace: WorkspaceConfig,
    dry_run: bool = False,
    logger=None,
) -> tuple[bool, list[str]]:
    """git rebase origin/<base_branch>. Returns (success, conflict_files)."""
    repo_dir = repo_path(workspace, repo_name)
    try:
        run_command(
            ["git", "rebase", f"origin/{base_branch}"],
            cwd=repo_dir,
            dry_run=dry_run,
            logger=logger,
        )
        return True, []
    except ProcessError:
        conflicts = get_conflict_files(repo_name, workspace=workspace)
        if conflicts:
            return False, conflicts
        raise


def force_push_branch(
    repo_name: str,
    branch_name: str,
    *,
    workspace: WorkspaceConfig,
    auth: GitAuthProvider,
    dry_run: bool = False,
    logger=None,
) -> None:
    """Push with --force-with-lease (post-rebase of feature branch)."""
    repo_dir = repo_path(workspace, repo_name)
    _run_git_remote(
        ["push", "--force-with-lease", "origin", branch_name],
        repo_dir=repo_dir,
        auth=auth,
        dry_run=dry_run,
        logger=logger,
    )


def push_branch_simple(
    repo_name: str,
    branch_name: str,
    *,
    workspace: WorkspaceConfig,
    auth: GitAuthProvider,
    dry_run: bool = False,
    logger=None,
) -> None:
    """Push branch without state check. For integration operations."""
    repo_dir = repo_path(workspace, repo_name)
    _run_git_remote(
        ["push", "-u", "origin", branch_name],
        repo_dir=repo_dir,
        auth=auth,
        dry_run=dry_run,
        logger=logger,
    )


def has_pending_rebase(repo_name: str, *, workspace: WorkspaceConfig) -> bool:
    """Check if .git/rebase-merge/ or .git/rebase-apply/ exist."""
    repo_dir = repo_path(workspace, repo_name)
    return (repo_dir / ".git" / "rebase-merge").exists() or (repo_dir / ".git" / "rebase-apply").exists()


def has_pending_merge(repo_name: str, *, workspace: WorkspaceConfig) -> bool:
    """Check if .git/MERGE_HEAD exists."""
    repo_dir = repo_path(workspace, repo_name)
    return (repo_dir / ".git" / "MERGE_HEAD").exists()


def get_conflict_files(repo_name: str, *, workspace: WorkspaceConfig) -> list[str]:
    """git diff --name-only --diff-filter=U."""
    repo_dir = repo_path(workspace, repo_name)
    try:
        result = run_command(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=repo_dir,
        )
        if not result.stdout:
            return []
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except ProcessError:
        return []


def log_diff(
    repo_name: str,
    from_ref: str,
    to_ref: str,
    *,
    workspace: WorkspaceConfig,
    dry_run: bool = False,
    logger=None,
) -> list[str]:
    """git log --oneline <from_ref>..<to_ref>. Returns list of lines."""
    repo_dir = repo_path(workspace, repo_name)
    result = run_command(
        ["git", "log", "--oneline", f"{from_ref}..{to_ref}"],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )
    if not result.stdout:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def delete_local_branch(
    repo_name: str,
    branch_name: str,
    *,
    workspace: WorkspaceConfig,
    dry_run: bool = False,
    logger=None,
) -> bool:
    repo_cfg = workspace.repos.get(repo_name)
    if not repo_cfg:
        raise ValidationError(f"Missing repo config for {repo_name}")
    repo_dir = repo_path(workspace, repo_name)
    base_branch = repo_cfg.base_branch
    current = current_branch(repo_name, workspace, dry_run=dry_run, logger=logger)
    if current == branch_name:
        run_command(["git", "checkout", base_branch], cwd=repo_dir, dry_run=dry_run, logger=logger)
    try:
        run_command(
            ["git", "show-ref", "--verify", f"refs/heads/{branch_name}"],
            cwd=repo_dir,
            dry_run=dry_run,
            logger=logger,
        )
    except ProcessError:
        if logger:
            logger.warn(f"Branch {branch_name} not found in {repo_name}; skipping delete.")
        return False
    run_command(["git", "branch", "-D", branch_name], cwd=repo_dir, dry_run=dry_run, logger=logger)
    return True
