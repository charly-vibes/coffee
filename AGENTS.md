<!-- TURU:START -->
# Whisper knowledge workspace (managed by turu — regenerate with `turu sync`)

- Workspace root: `/var/home/sasha/.whisper`
- Repo key: `cv/charly-vibes/coffee` · Branch slug: `main` · Worktree slot: `.git`
- Deterministic routing (resolve, never guess):
- global → `/var/home/sasha/.whisper/rules.md`
- repo → `/var/home/sasha/.whisper/repos/cv/charly-vibes/coffee/env.md`
- branch → `/var/home/sasha/.whisper/repos/cv/charly-vibes/coffee/branches/main/notes.md`
- worktree → `/var/home/sasha/.whisper/repos/cv/charly-vibes/coffee/worktrees/.git/env.md`
- Commands: `turu resolve <scope>` · `turu append <scope> --text ... [--topic k] [--supersedes id]` · `turu recall <scope> [--topic k] [--budget bytes]` · `turu distill <scope> --begin|--commit` · `turu bundle pack|unpack` · `turu status` · `turu doctor` · `turu feedback <kind>`

<!-- TURU:END -->


<!-- OPENSPEC:START -->
# OpenSpec Instructions

These instructions are for AI assistants working in this project.

Always open `@/openspec/AGENTS.md` when the request:
- Mentions planning or proposals (words like proposal, spec, change, plan)
- Introduces new capabilities, breaking changes, architecture shifts, or big performance/security work
- Sounds ambiguous and you need the authoritative spec before coding

Use `@/openspec/AGENTS.md` to learn:
- How to create and apply change proposals
- Spec format and conventions
- Project structure and guidelines

Keep this managed block so 'openspec update' can refresh the instructions.

<!-- OPENSPEC:END -->

# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd prime` for full workflow context.

> **Architecture in one line:** issues live in `.beads/issues.jsonl` (JSONL, committed to git — the source of truth). bd runs **embedded** (in-process Dolt, `no-db: true`): there is no dolt remote and no `bd dolt push/pull` sync. Git itself is the sync mechanism.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
```

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in `.beads/issues.jsonl` (JSONL in git, source of truth); bd runs embedded with `no-db: true` — no dolt remote, no `bd dolt push/pull`. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->

## Git & Workflow Discipline

- **Never use `git add -A`** — always stage specific files with explicit paths
- **Per-ticket pipeline**: always follow `TDD → ro5u → fix → commit → next ticket`

## Project

- `README.md` documents the data layout, scripts, and known data gaps (originally `data/TRACKING.md` in microdancing)
- `openspec/project.md` describes conventions and reuse notes
