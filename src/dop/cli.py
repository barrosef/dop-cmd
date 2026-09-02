"""CLI for IA-First dop operations (v0.4 — fluxo conversacional, sem stages)."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .config import get_workspace
from .core.errors import MCPError, SecurityViolationError, ValidationError
from .core.fs import write_text
from .core.logging_utils import get_logger
from .core.security import guard_text, redact
from .core.state import (
    add_linked_jira,
    append_command_log,
    append_pr_record,
    impacted_repos,
    load_state,
    resolve_alias,
    save_state,
    set_repo_impacted,
    set_repo_skipped,
    validate_jira_key,
    write_alias,
)
from .git import (
    checkout_branch,
    checkout_new_branch_from_remote,
    commit_changes,
    create_branch,
    current_branch,
    fetch_origin,
    force_push_branch,
    get_conflict_files,
    has_pending_merge,
    has_pending_rebase,
    has_uncommitted_changes,
    list_local_branches,
    log_diff,
    merge_remote_branch,
    pull_branch_if_exists,
    push_branch,
    rebase_on_base,
    repo_path,
)
from .git.auth import build_auth_provider
from .platform import build_platform_provider

STATE_FILE_NAME = ".state.json"
LOGS_DIR_NAME = "logs"

DESENV_BRANCH = "desenv"
MERGE_CONFLICTS_BRANCH = "merge-conflicts-desenv-from-OG-GLOBAL"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _demands_root(workspace) -> Path:
    return Path(workspace.root) / workspace.demands_dir


def _demand_dir(workspace, jira_key: str) -> Path:
    return _demands_root(workspace) / jira_key


def _state_path(workspace, jira_key: str) -> Path:
    return _demand_dir(workspace, jira_key) / STATE_FILE_NAME


def _logs_dir(workspace, jira_key: str) -> Path:
    return _demand_dir(workspace, jira_key) / LOGS_DIR_NAME


def _repo_order(workspace) -> list[str]:
    return list(workspace.repos.keys())


def _long_branches(workspace, repo_name: str) -> list[str]:
    repo_cfg = workspace.repos.get(repo_name)
    if repo_cfg and repo_cfg.long_branches is not None:
        return list(repo_cfg.long_branches)
    return list(workspace.long_branches)


def _command_string(args: argparse.Namespace) -> str:
    parts = [args.command]
    for attr in ("jira_key", "linked_keys"):
        val = getattr(args, attr, None)
        if val:
            parts.append(",".join(val) if isinstance(val, list) else str(val))
    for flag, attr in [
        ("--repo", "repo"),
        ("--repos", "repos"),
        ("--branch", "branch"),
        ("--source-branch", "source_branch"),
        ("--title", "title"),
        ("--linked", "linked"),
        ("--workspace", "workspace"),
    ]:
        val = getattr(args, attr, None)
        if val:
            if isinstance(val, list):
                parts.extend([flag, ",".join(val)])
            else:
                parts.extend([flag, str(val)])
    if getattr(args, "force", False):
        parts.append("--force")
    return " ".join(parts)


def _load_state(workspace, jira_key: str, dry_run: bool):
    return load_state(
        jira_key,
        _state_path(workspace, jira_key),
        jira_base_url=workspace.jira_base_url,
        create=not dry_run,
    )


def _resolve_jira_key(workspace, jira_key: str) -> str:
    """Resolve aliases. Se jira_key é alias, retorna a chave mestre."""
    return resolve_alias(_demands_root(workspace), jira_key)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Azure helpers (PR URL → org/project env)
# ---------------------------------------------------------------------------

def _set_azure_defaults_from_prs(pr_results: list[dict], workspace) -> None:
    org_env = workspace.platform_config.org_env or "AZURE_DEVOPS_ORG"
    project_env = workspace.platform_config.project_env or "AZURE_DEVOPS_PROJECT"
    if os.environ.get(org_env) and os.environ.get(project_env):
        return
    for pr in pr_results:
        web_url = pr.get("web_url")
        if not web_url:
            continue
        parsed = urlparse(web_url)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            continue
        if not os.environ.get(org_env):
            os.environ[org_env] = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/"
        if not os.environ.get(project_env):
            os.environ[project_env] = parts[1]
        break


# ---------------------------------------------------------------------------
# Teams message formatting
# ---------------------------------------------------------------------------

def format_teams_message(jira_key: str, jira_url: str | None, linked: list[str], pr_results: list[dict]) -> str:
    keys = [jira_key] + list(linked)
    header_keys = " + ".join(keys)
    link = jira_url or ""
    lines = [f"PRs - {header_keys} - {link}"]
    for index, pr in enumerate(pr_results, start=1):
        url = pr.get("web_url") or "<link indisponivel>"
        conflict = "Sim" if pr.get("has_conflict") else "Não"
        lines.extend([
            f"PR {index}",
            f"App: {pr.get('repo')}",
            f"From {pr.get('source_branch')} to {pr.get('target_branch')}",
            f"Link: {url}",
            f"Conflito: {conflict}",
            "",
        ])
    return "\n".join(lines).rstrip()


def write_teams_message(jira_key: str, jira_url: str | None, linked: list[str], pr_results: list[dict], *, workspace, dry_run: bool) -> str:
    message = format_teams_message(jira_key, jira_url, linked, pr_results)
    path = _demand_dir(workspace, jira_key) / "03-pr-team-message.md"
    write_text(path, message + "\n", dry_run=dry_run)
    return str(path)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_demand_init(args: argparse.Namespace, logger, workspace, auth) -> int:
    """Inicializa demanda: registra repos impactados, atualiza branches longas, cria branches de trabalho."""
    jira_key = args.jira_key
    state, original = _load_state(workspace, jira_key, args.dry_run)

    impacted = _split_csv(args.repos)
    if not impacted:
        raise ValidationError("--repos é obrigatório (lista CSV de repos impactados).")
    unknown = [r for r in impacted if r not in workspace.repos]
    if unknown:
        raise ValidationError(f"Repo(s) desconhecido(s): {', '.join(unknown)}")

    branch_name = args.branch or jira_key
    all_repos = _repo_order(workspace)

    # Marca impacted/skipped
    for repo in all_repos:
        if repo in impacted:
            set_repo_impacted(state, repo, branch_name)
        else:
            set_repo_skipped(state, repo)

    # Linked Jiras
    linked = _split_csv(args.linked)
    for linked_key in linked:
        validate_jira_key(linked_key, pattern=workspace.jira_key_pattern)
        if add_linked_jira(state, linked_key):
            write_alias(_demands_root(workspace), linked_key, jira_key, dry_run=args.dry_run)
            logger.info(f"Alias gravado: {linked_key} -> {jira_key}")

    # Atualiza branches longas dos repos impactados
    for repo in impacted:
        logger.info(f"Atualizando branches longas em {repo}...")
        for long_branch in _long_branches(workspace, repo):
            try:
                pull_branch_if_exists(repo, long_branch, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)
            except Exception as exc:
                logger.warn(f"  {repo}: falha em pull '{long_branch}': {exc}")

    # Cria branches de trabalho a partir da base e empurra para remote
    for repo in impacted:
        repo_cfg = workspace.repos[repo]
        existing = list_local_branches(repo, workspace, dry_run=args.dry_run, logger=logger)
        if branch_name in existing:
            logger.info(f"  {repo}: branch '{branch_name}' já existe local; skipping create.")
            checkout_branch(repo, branch_name, workspace=workspace, dry_run=args.dry_run, logger=logger)
        else:
            logger.info(f"Criando branch '{branch_name}' em {repo} (base: {repo_cfg.base_branch})...")
            create_branch(repo, branch_name, workspace, auth, pull_first=False, dry_run=args.dry_run, logger=logger)
        # Push inicial (cria branch no remote)
        try:
            push_branch(repo, branch_name, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)
        except Exception as exc:
            logger.warn(f"  {repo}: push inicial falhou ({exc}). Continue manualmente se necessário.")

    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, jira_key), state, original, dry_run=args.dry_run)
    logger.info(f"demand-init OK. Repos impactados: {', '.join(impacted)}. Branch: {branch_name}.")
    return 0


def handle_link(args: argparse.Namespace, logger, workspace, auth) -> int:
    """Adiciona Jira(s) agrupada(s) numa demanda já aberta."""
    master_key = args.jira_key
    state, original = _load_state(workspace, master_key, args.dry_run)
    added: list[str] = []
    for linked_key in args.linked_keys:
        validate_jira_key(linked_key, pattern=workspace.jira_key_pattern)
        if add_linked_jira(state, linked_key):
            write_alias(_demands_root(workspace), linked_key, master_key, dry_run=args.dry_run)
            added.append(linked_key)
    if not added:
        logger.info("Nenhum link novo (já estavam registrados).")
    else:
        logger.info(f"Linked: {', '.join(added)} -> {master_key}")
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, master_key), state, original, dry_run=args.dry_run)
    return 0


def handle_update_repos(args: argparse.Namespace, logger, workspace, auth) -> int:
    """Pull de todas as branches longas conhecidas em cada repo (silencioso se branch não existe)."""
    repos = [args.repo] if args.repo else _repo_order(workspace)
    for repo in repos:
        if repo not in workspace.repos:
            raise ValidationError(f"Repo desconhecido: {repo}")
        logger.info(f"== {repo} ==")
        for long_branch in _long_branches(workspace, repo):
            try:
                pull_branch_if_exists(repo, long_branch, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)
            except Exception as exc:
                logger.warn(f"  falha em pull '{long_branch}': {exc}")
    logger.info("update-repos OK.")
    return 0


def handle_git_push(args: argparse.Namespace, logger, workspace, auth) -> int:
    """Push da branch de uma demanda. Sem gates."""
    jira_key = args.jira_key
    state, original = _load_state(workspace, jira_key, args.dry_run)
    repos = [args.repo] if args.repo else impacted_repos(state)
    if not repos:
        raise ValidationError(f"Nenhum repo impactado registrado em {jira_key}. Rode demand-init antes ou passe --repo.")
    for repo in repos:
        repo_state = state.get("repos", {}).get(repo, {})
        branch = args.branch or repo_state.get("branch") or jira_key
        push_fn = force_push_branch if args.force else push_branch
        push_fn(repo, branch, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, jira_key), state, original, dry_run=args.dry_run)
    logger.info("git-push OK.")
    return 0


def handle_pr_publish(args: argparse.Namespace, logger, workspace, auth) -> int:
    """Commit pendente (se houver) + push + cria PRs para todos pr_targets do repo."""
    jira_key = args.jira_key
    state, original = _load_state(workspace, jira_key, args.dry_run)
    repo = args.repo
    if repo not in workspace.repos:
        raise ValidationError(f"Repo desconhecido: {repo}")
    repo_cfg = workspace.repos[repo]
    repo_state = state.get("repos", {}).get(repo, {})
    branch = args.source_branch or repo_state.get("branch") or jira_key

    # Commit pendente
    if has_uncommitted_changes(repo, workspace=workspace, logger=logger):
        commit_msg = args.commit_message or f"{jira_key}: {args.title.split(':', 1)[-1].strip()}"
        logger.info(f"Commit em {repo}: {commit_msg}")
        commit_changes(repo, commit_msg, workspace=workspace, dry_run=args.dry_run, logger=logger)

    # Garante checkout da feature antes de push
    cur = current_branch(repo, workspace, dry_run=args.dry_run, logger=logger)
    if cur != branch and not args.dry_run:
        checkout_branch(repo, branch, workspace=workspace, dry_run=args.dry_run, logger=logger)

    # Push (force-with-lease se solicitado)
    push_fn = force_push_branch if args.force else push_branch
    push_fn(repo, branch, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)

    # Cria PRs em todos os targets configurados (ou apenas args.target se passado)
    targets = [args.target] if args.target else list(repo_cfg.pr_targets)
    platform = build_platform_provider(workspace)
    new_prs: list[dict] = []
    for target in targets:
        # Reusa PR ativo existente para mesma source→target (Azure DevOps permite
        # duplicatas; sem essa checagem cada pr-publish do mesmo branch cria PR
        # novo). --force-new-pr permite forçar criação se necessário.
        reused = False
        if not getattr(args, "force_new_pr", False):
            try:
                existing = platform.list_prs(
                    repo_name=repo,
                    source_branch=branch,
                    target_branch=target,
                    dry_run=args.dry_run,
                    logger=logger,
                )
            except Exception as e:
                logger.warn(f"Falha ao listar PRs existentes ({repo} {branch}->{target}): {e}. Seguindo com create.")
                existing = []
            if existing:
                pr = existing[0]
                reused = True

        if not reused:
            pr = platform.create_pr(
                repo_name=repo,
                source_branch=branch,
                target_branch=target,
                title=args.title,
                description=args.body or f"Jira: {state.get('jiraUrl', '')}",
                reviewers=None,
                dry_run=args.dry_run,
                logger=logger,
            )
        record = {
            "repo": repo,
            "source_branch": pr.source_branch,
            "target_branch": pr.target_branch,
            "pr_id": pr.pr_id,
            "web_url": pr.web_url,
            "merge_status": pr.merge_status,
            "has_conflict": pr.has_conflict,
        }
        append_pr_record(state, record)
        new_prs.append(record)
        flag = " (CONFLITO)" if pr.has_conflict else ""
        verb = "reaproveitado" if reused else "criado"
        logger.info(f"PR #{pr.pr_id} {verb}: {repo} {branch} -> {target}{flag}  {pr.web_url}")

    # Volta o repo para a base branch (UX consistente com o fluxo antigo)
    try:
        checkout_branch(repo, repo_cfg.base_branch, workspace=workspace, dry_run=args.dry_run, logger=logger)
    except Exception:
        logger.warn(f"Não foi possível voltar {repo} para {repo_cfg.base_branch}.")

    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, jira_key), state, original, dry_run=args.dry_run)

    if any(pr["has_conflict"] for pr in new_prs):
        logger.warn("Algum PR foi criado com conflitos. Resolva antes de gerar a mensagem Teams.")
    return 0


def handle_teams_message(args: argparse.Namespace, logger, workspace, auth) -> int:
    """Gera 03-pr-team-message.md a partir de state['prs']."""
    jira_key = args.jira_key
    state, original = _load_state(workspace, jira_key, args.dry_run)
    pr_results = state.get("prs") or []
    if not pr_results:
        raise ValidationError(f"Nenhum PR registrado para {jira_key}. Rode pr-publish antes.")
    linked = state.get("linkedJiraKeys") or []
    path = write_teams_message(
        jira_key, state.get("jiraUrl"), linked, pr_results,
        workspace=workspace, dry_run=args.dry_run,
    )
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, jira_key), state, original, dry_run=args.dry_run)
    print(format_teams_message(jira_key, state.get("jiraUrl"), linked, pr_results))
    logger.info(f"Mensagem Teams escrita em {path}.")
    return 0


# ---------------------------------------------------------------------------
# Conflict resolution (feature branch rebase) — sem stage gates
# ---------------------------------------------------------------------------

def handle_solve_conflict(args: argparse.Namespace, logger, workspace, auth) -> int:
    jira_key = args.jira_key
    state, original = _load_state(workspace, jira_key, args.dry_run)

    target_repos = [args.repo] if args.repo else impacted_repos(state)
    if not target_repos:
        raise ValidationError(f"Nenhum repo impactado em {jira_key}.")

    handled: list[str] = []
    for repo in target_repos:
        repo_cfg = workspace.repos.get(repo)
        if not repo_cfg:
            raise ValidationError(f"Repo desconhecido: {repo}")
        repo_state = state.get("repos", {}).get(repo, {})
        branch = repo_state.get("branch") or jira_key
        base_branch = repo_cfg.base_branch

        fetch_origin(repo, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)
        checkout_branch(repo, branch, workspace=workspace, dry_run=args.dry_run, logger=logger)

        success, conflicts = rebase_on_base(repo, base_branch, workspace=workspace, dry_run=args.dry_run, logger=logger)
        if success:
            logger.info(f"Rebase sem conflitos em {repo}. Faça force-push: dop git-push {jira_key} --repo {repo} --force")
            handled.append(repo)
        else:
            logger.warn(f"Conflitos durante rebase em {repo}:")
            for f in conflicts:
                logger.warn(f"  {f}")
            logger.info("Resolva manualmente: git add <arquivos> && git rebase --continue")
            logger.info(f"Quando terminar: dop git-push {jira_key} --repo {repo} --force")
            break  # para no primeiro repo com conflito

    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, jira_key), state, original, dry_run=args.dry_run)
    return 0


# ---------------------------------------------------------------------------
# Integration handlers (sem JIRA key) — operações ortogonais
# ---------------------------------------------------------------------------

def handle_integrate_desenv(args: argparse.Namespace, logger, workspace, auth) -> int:
    """OG-GLOBAL/hml -> desenv: cria PR diário se houver commits novos."""
    platform = build_platform_provider(workspace)
    repos = [args.repo] if args.repo else _repo_order(workspace)
    today = datetime.now().strftime("%Y-%m-%d")

    summary: list[str] = []
    conflict_repos: list[str] = []

    for repo in repos:
        repo_cfg = workspace.repos.get(repo)
        if not repo_cfg:
            raise ValidationError(f"Repo desconhecido: {repo}")
        base_branch = repo_cfg.base_branch
        if base_branch == DESENV_BRANCH:
            summary.append(f"{repo:<25} base é {DESENV_BRANCH} -- skip")
            continue

        fetch_origin(repo, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)
        commits = log_diff(repo, f"origin/{DESENV_BRANCH}", f"origin/{base_branch}", workspace=workspace)
        if not commits:
            logger.info(f"{repo}: {DESENV_BRANCH} já atualizado")
            summary.append(f"{repo:<25} sem commits novos -- skip")
            continue

        existing = platform.list_prs(repo_name=repo, source_branch=base_branch, target_branch=DESENV_BRANCH, dry_run=args.dry_run, logger=logger)
        if existing:
            pr = existing[0]
            note = " (CONFLITOS)" if pr.has_conflict else ""
            summary.append(f"{repo:<25} PR #{pr.pr_id} já existe{note}")
            if pr.has_conflict:
                conflict_repos.append(repo)
            continue

        description = "Integração automática. Commits incluídos:\n" + "\n".join(commits)
        title = f"Integração diária {base_branch} -> {DESENV_BRANCH} ({today}) - {repo}"
        pr = platform.create_pr(
            repo_name=repo, source_branch=base_branch, target_branch=DESENV_BRANCH,
            title=title, description=description, dry_run=args.dry_run, logger=logger,
        )
        if pr.has_conflict:
            summary.append(f"{repo:<25} PR #{pr.pr_id} criado (CONFLITOS)")
            conflict_repos.append(repo)
            logger.warn(f"{repo}: PR #{pr.pr_id} criado com conflitos.")
        else:
            summary.append(f"{repo:<25} PR #{pr.pr_id} criado (ok)")
            logger.info(f"{repo}: PR #{pr.pr_id} criado sem conflitos.")

    print(f"\n=== Integração diária base -> {DESENV_BRANCH} ({today}) ===")
    for line in summary:
        print(line)
    if conflict_repos:
        print(f"\nRepos com conflito: {', '.join(conflict_repos)}")
        for r in conflict_repos:
            print(f"-> dop prepare-merge-conflicts --repo {r}")
    print()
    return 0


def handle_prepare_merge_conflicts(args: argparse.Namespace, logger, workspace, auth) -> int:
    repos = [args.repo] if args.repo else _repo_order(workspace)
    summary: list[str] = []
    for repo in repos:
        repo_cfg = workspace.repos.get(repo)
        if not repo_cfg:
            raise ValidationError(f"Repo desconhecido: {repo}")
        base_branch = repo_cfg.base_branch
        r_dir = repo_path(workspace, repo)
        fetch_origin(repo, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)
        checkout_new_branch_from_remote(repo, MERGE_CONFLICTS_BRANCH, DESENV_BRANCH, workspace=workspace, dry_run=args.dry_run, logger=logger)
        success, conflicts = merge_remote_branch(repo, base_branch, workspace=workspace, dry_run=args.dry_run, logger=logger)
        if success:
            logger.info(f"Merge sem conflitos em {repo}. Execute finish-merge-conflicts.")
            summary.append(f"{repo}: merge sem conflitos")
        else:
            logger.warn(f"Conflitos em {repo}:")
            for f in conflicts:
                logger.warn(f"  {f}")
            logger.info(f"Resolva manualmente em {r_dir} (git add + git commit) e rode dop finish-merge-conflicts --repo {repo}")
            summary.append(f"{repo}: CONFLITOS ({len(conflicts)} arquivos)")
    print("\n=== Prepare Merge Conflicts ===")
    for line in summary:
        print(line)
    return 0


def handle_finish_merge_conflicts(args: argparse.Namespace, logger, workspace, auth) -> int:
    platform = build_platform_provider(workspace)
    repos = [args.repo] if args.repo else _repo_order(workspace)
    today = datetime.now().strftime("%Y-%m-%d")
    summary: list[str] = []
    for repo in repos:
        repo_cfg = workspace.repos.get(repo)
        if not repo_cfg:
            raise ValidationError(f"Repo desconhecido: {repo}")
        cur = current_branch(repo, workspace, logger=logger)
        if cur != MERGE_CONFLICTS_BRANCH:
            branches = list_local_branches(repo, workspace, logger=logger)
            if MERGE_CONFLICTS_BRANCH not in branches:
                raise ValidationError(f"Branch {MERGE_CONFLICTS_BRANCH} não existe em {repo}. Rode prepare-merge-conflicts primeiro.")
            raise ValidationError(f"Branch atual em {repo} é '{cur}'. Faça checkout {MERGE_CONFLICTS_BRANCH} primeiro.")
        if has_pending_merge(repo, workspace=workspace):
            raise ValidationError(f"Merge incompleto em {repo}. Resolva e commite antes.")
        commits = log_diff(repo, f"origin/{DESENV_BRANCH}", "HEAD", workspace=workspace)
        if not commits:
            raise ValidationError(f"Nenhum commit a publicar em {repo}.")
        push_branch(repo, MERGE_CONFLICTS_BRANCH, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)
        existing = platform.list_prs(repo_name=repo, source_branch=MERGE_CONFLICTS_BRANCH, target_branch=DESENV_BRANCH, dry_run=args.dry_run, logger=logger)
        if existing:
            pr = existing[0]
            summary.append(f"{repo}: PR #{pr.pr_id} já existe")
            continue
        title = f"Integração base -> {DESENV_BRANCH} com resolução de conflitos ({today}) - {repo}"
        description = f"Resolução de conflitos da integração base -> {DESENV_BRANCH}."
        pr = platform.create_pr(repo_name=repo, source_branch=MERGE_CONFLICTS_BRANCH, target_branch=DESENV_BRANCH, title=title, description=description, dry_run=args.dry_run, logger=logger)
        summary.append(f"{repo}: PR #{pr.pr_id} criado")
    print("\n=== Finish Merge Conflicts ===")
    for line in summary:
        print(line)
    return 0


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------

def handle_show(args: argparse.Namespace, logger, workspace, auth) -> int:
    """Imprime o .state.json da demanda (resolvendo alias)."""
    jira_key = args.jira_key
    state_path = _state_path(workspace, jira_key)
    if not state_path.exists():
        raise ValidationError(f"Sem .state.json para {jira_key} em {state_path}.")
    print(state_path.read_text(encoding="utf-8"))
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class SecureArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        safe = redact(message)
        self.print_usage(sys.stderr)
        raise ValidationError(safe)


def build_parser() -> argparse.ArgumentParser:
    parser = SecureArgumentParser(prog="dop")
    parser.add_argument("--version", "-v", action="version", version=f"dop {__version__}",
                        help="Mostra a versão do dop e sai")
    parser.add_argument("--dry-run", action="store_true", help="Loga sem executar git/az")
    parser.add_argument("--workspace", default=None, help="Nome do workspace (auto-detect via CWD)")
    sub = parser.add_subparsers(dest="command", required=True)

    # demand-init
    p = sub.add_parser("demand-init", help="Inicializa demanda: registra repos impactados, atualiza branches longas, cria branches de trabalho")
    p.add_argument("jira_key")
    p.add_argument("--repos", required=True, help="CSV de repos impactados (ex: lifesupport-api,optum-support-be)")
    p.add_argument("--linked", default=None, help="CSV de Jiras agrupadas (cria aliases)")
    p.add_argument("--branch", default=None, help="Nome da branch (default: <jira_key>)")
    p.set_defaults(func=handle_demand_init, command="demand-init")

    # link
    p = sub.add_parser("link", help="Adiciona Jira(s) agrupada(s) numa demanda existente")
    p.add_argument("jira_key", help="Jira mestre")
    p.add_argument("linked_keys", nargs="+", help="Chaves a agrupar")
    p.set_defaults(func=handle_link, command="link")

    # update-repos
    p = sub.add_parser("update-repos", help="Pull em todas as branches longas conhecidas")
    p.add_argument("--repo", default=None)
    p.set_defaults(func=handle_update_repos, command="update-repos")

    # git-push
    p = sub.add_parser("git-push", help="Push da branch da demanda")
    p.add_argument("jira_key")
    p.add_argument("--repo", default=None)
    p.add_argument("--branch", default=None)
    p.add_argument("--force", action="store_true", help="force-with-lease (pós-rebase)")
    p.set_defaults(func=handle_git_push, command="git-push")

    # pr-publish
    p = sub.add_parser("pr-publish", help="Commit pendente + push + cria PRs (todos pr_targets do repo)")
    p.add_argument("jira_key")
    p.add_argument("--repo", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body", default=None, help="Descrição do PR (default: link Jira)")
    p.add_argument("--source-branch", default=None, help="Branch da feature (default: state.repos[<repo>].branch ou jira_key)")
    p.add_argument("--target", default=None, help="Branch alvo única (default: todos pr_targets)")
    p.add_argument("--commit-message", default=None, help="Mensagem de commit se houver pendência (default derivado do title)")
    p.add_argument("--force", action="store_true")
    p.add_argument(
        "--force-new-pr",
        action="store_true",
        help="Cria PR mesmo se já existir um ativo para mesma source→target (default: reaproveita)",
    )
    p.set_defaults(func=handle_pr_publish, command="pr-publish")

    # teams-message
    p = sub.add_parser("teams-message", help="Gera 03-pr-team-message.md a partir do .state.json")
    p.add_argument("jira_key")
    p.set_defaults(func=handle_teams_message, command="teams-message")

    # solve-conflict
    p = sub.add_parser("solve-conflict", help="Rebase da feature branch sobre a base (resolve conflitos)")
    p.add_argument("jira_key")
    p.add_argument("--repo", default=None)
    p.set_defaults(func=handle_solve_conflict, command="solve-conflict")

    # integrate-desenv (no JIRA)
    p = sub.add_parser("integrate-desenv", help="Cria PR base -> desenv para integração diária")
    p.add_argument("--repo", default=None)
    p.set_defaults(func=handle_integrate_desenv, command="integrate-desenv")

    p = sub.add_parser("prepare-merge-conflicts", help="Cria branch local merge-conflicts e tenta merge")
    p.add_argument("--repo", default=None)
    p.set_defaults(func=handle_prepare_merge_conflicts, command="prepare-merge-conflicts")

    p = sub.add_parser("finish-merge-conflicts", help="Push da branch merge-conflicts e cria PR para desenv")
    p.add_argument("--repo", default=None)
    p.set_defaults(func=handle_finish_merge_conflicts, command="finish-merge-conflicts")

    # show
    p = sub.add_parser("show", help="Imprime .state.json da demanda")
    p.add_argument("jira_key")
    p.set_defaults(func=handle_show, command="show")

    # ── Runtime lifecycle ──
    from .runtime.handlers import (
        handle_start as _rt_start,
        handle_stop as _rt_stop,
        handle_log as _rt_log,
        handle_status as _rt_status,
        handle_restart as _rt_restart,
        handle_rebuild as _rt_rebuild,
        handle_e2e as _rt_e2e,
        handle_aaa as _rt_aaa,
        handle_it as _rt_it,
        handle_codegen as _rt_codegen,
        handle_report as _rt_report,
        handle_clean as _rt_clean,
    )

    def _make_rt_func(rt_handler):
        """Wrap a runtime handler to match the CLI dispatch signature (args, logger, workspace, auth)."""
        def _func(args, logger, workspace, auth):
            return rt_handler(workspace, args, dry_run=args.dry_run, logger=logger)
        _func.__name__ = rt_handler.__name__
        return _func

    p_start = sub.add_parser("start", help="Sobe apps via docker compose")
    p_start.add_argument("apps", nargs="+", help="Apps to start (names or aliases)")
    p_start.add_argument("--no-deps", action="store_true", help="Don't auto-start FE->BE deps")
    p_start.set_defaults(func=_make_rt_func(_rt_start), command="start")

    p_stop = sub.add_parser("stop", help="Para apps (ou todos)")
    p_stop.add_argument("apps", nargs="*", help="Apps to stop (empty = all)")
    p_stop.set_defaults(func=_make_rt_func(_rt_stop), command="stop")

    p_restart = sub.add_parser("restart", help="Restart apps")
    p_restart.add_argument("apps", nargs="+")
    p_restart.add_argument("--no-deps", action="store_true")
    p_restart.set_defaults(func=_make_rt_func(_rt_restart), command="restart")

    p_rebuild = sub.add_parser("rebuild", help="Rebuild FE (npm build + restart nginx)")
    p_rebuild.add_argument("apps", nargs="+", help="FE apps to rebuild (osf, pfe, cef)")
    p_rebuild.set_defaults(func=_make_rt_func(_rt_rebuild), command="rebuild")

    p_status = sub.add_parser("status", help="Status dos containers")
    p_status.set_defaults(func=_make_rt_func(_rt_status), command="status")

    p_ps = sub.add_parser("ps", help="Alias para status")
    p_ps.set_defaults(func=_make_rt_func(_rt_status), command="ps")

    p_log = sub.add_parser("log", help="Logs de apps")
    p_log.add_argument("apps", nargs="+")
    p_log.add_argument("--follow", "-f", action="store_true", default=True)
    p_log.add_argument("--tail", "-n", type=int, default=None)
    p_log.add_argument("--since", default=None)
    p_log.set_defaults(func=_make_rt_func(_rt_log), command="log")

    # ── E2E ──
    p_e2e = sub.add_parser("e2e", help="Run Playwright e2e tests")
    p_e2e.add_argument("targets", nargs="*", help="Suite name, JIRA key, or file path")
    p_e2e.add_argument("--headed", action="store_true", help="Visual mode (X11)")
    p_e2e.add_argument("--max-strikes", type=int, default=None, dest="max_strikes", help="Max consecutive reds (1-10)")
    p_e2e.add_argument("--reset-strikes", action="store_true")
    p_e2e.add_argument("-k", dest="k", default=None, help="pytest -k filter")
    p_e2e.add_argument(
        "--fresh-report",
        action="store_true",
        dest="fresh_report",
        help="Reconstrói o .allure-results da suíte do zero a partir de TODAS as "
             "runs em reports/ (descarta apenas resíduos sem run-N de origem)",
    )
    p_e2e.set_defaults(func=_make_rt_func(_rt_e2e), command="e2e")

    # ── AAA (unit, Java) ──
    p_aaa = sub.add_parser("aaa", help="Run unit AAA tests (Maven, host)")
    p_aaa.add_argument("targets", nargs="*", help="Repo name(s) or 'all'")
    p_aaa.add_argument("-k", dest="k", default=None, help="Maven -Dtest filter")
    p_aaa.add_argument("--fresh-report", action="store_true", dest="fresh_report",
                       help="Zera o .allure-results do projeto antes de agregar")
    p_aaa.set_defaults(func=_make_rt_func(_rt_aaa), command="aaa")

    # ── IT (integração, Testcontainers) ──
    p_it = sub.add_parser("it", help="Run integration tests (Maven failsafe, host)")
    p_it.add_argument("targets", nargs="*", help="Repo name(s) or 'all'")
    p_it.add_argument("-k", dest="k", default=None, help="Maven -Dit.test filter")
    p_it.add_argument("--fresh-report", action="store_true", dest="fresh_report",
                      help="Zera o .allure-results do projeto antes de agregar")
    p_it.set_defaults(func=_make_rt_func(_rt_it), command="it")

    # ── Codegen ──
    p_codegen = sub.add_parser("codegen", help="Playwright codegen recording")
    p_codegen.add_argument("suite", help="Suite name")
    p_codegen.add_argument("--url", default=None, help="Start URL")
    p_codegen.add_argument("--out", default=None, help="Output file path")
    p_codegen.set_defaults(func=_make_rt_func(_rt_codegen), command="codegen")

    # ── Report ──
    p_report = sub.add_parser("report", help="Allure report management")
    report_subs = p_report.add_subparsers(dest="report_action")
    report_subs.add_parser("serve", help="Ensure allure is running")
    p_report_open = report_subs.add_parser("open", help="Open report in browser")
    p_report_open.add_argument("suite", nargs="?")
    p_report_open.add_argument("jira", nargs="?")
    p_report_clean = report_subs.add_parser("clean", help="Clean old runs")
    p_report_clean.add_argument("--keep", type=int, default=5)
    p_report.set_defaults(func=_make_rt_func(_rt_report), command="report")

    # ── Clean ──
    p_clean = sub.add_parser("clean", help="Remove Docker volumes/caches")
    p_clean.add_argument("--m2", action="store_true", help="Maven cache")
    p_clean.add_argument("--node-modules", action="store_true", dest="node_modules", help="node_modules volumes")
    p_clean.add_argument("--allure", action="store_true", help="Allure data")
    p_clean.add_argument("--all", action="store_true", dest="all", help="Everything")
    p_clean.set_defaults(func=_make_rt_func(_rt_clean), command="clean")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workspace = get_workspace(args.workspace)

    jira_key = getattr(args, "jira_key", None)
    if jira_key:
        validate_jira_key(jira_key, pattern=workspace.jira_key_pattern)
        # Resolve alias para a chave mestre antes de qualquer operação
        resolved = _resolve_jira_key(workspace, jira_key)
        if resolved != jira_key:
            args.jira_key = resolved
            jira_key = resolved
        logs_dir = _logs_dir(workspace, jira_key)
        logger = get_logger(jira_key, args.command, logs_dir=logs_dir)
        if resolved != getattr(args, "_orig_jira_key", jira_key):
            logger.info(f"Alias resolvido para chave mestre: {jira_key}")
    else:
        logs_dir = Path(workspace.root) / "logs"
        logger = get_logger("integration", args.command, logs_dir=logs_dir)

    auth = build_auth_provider(workspace)

    try:
        return args.func(args, logger, workspace, auth)
    except MCPError as exc:
        try:
            message = redact(str(exc))
            guard_text(message)
        except SecurityViolationError:
            message = "Security violation."
        print(f"ERROR: {message}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: Unexpected failure: {redact(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
