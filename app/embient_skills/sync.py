"""
Embient Skills sync — pulls latest skills from the upstream repository.

Clones or pulls the SKE-Labs/agent-trading-skills repo into a cache dir,
then copies the skill directories into app/embient_skills/data/.

Usage:
    result = sync_skills()        # one-shot sync
    start_periodic_sync(library)  # background task every N hours
"""

import asyncio
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_URL = "https://github.com/SKE-Labs/agent-trading-skills.git"
CACHE_DIR = Path(__file__).parent / ".repo_cache"
SKILLS_DATA_DIR = Path(__file__).parent / "data"

# Sync state (accessible for API status)
_last_sync: dict = {"timestamp": None, "status": "never", "added": 0, "updated": 0, "error": None}


def get_sync_status() -> dict:
    return dict(_last_sync)


def sync_skills() -> dict:
    """
    Pull latest skills from upstream repo and copy into data dir.
    Returns {"status": "ok"|"error", "added": N, "updated": N, "details": str}.
    """
    global _last_sync

    try:
        # Step 1: Clone or pull
        if (CACHE_DIR / ".git").exists():
            logger.info("SKILLS_SYNC: pulling latest from %s", REPO_URL)
            result = subprocess.run(
                ["git", "-C", str(CACHE_DIR), "pull", "--ff-only"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                # If pull fails (diverged), re-clone
                logger.warning("SKILLS_SYNC: pull failed, re-cloning: %s", result.stderr.strip())
                shutil.rmtree(CACHE_DIR, ignore_errors=True)
                _clone_repo()
        else:
            _clone_repo()

        # Step 2: Find skill directories in the repo
        # The repo structure might be: skills/<category>/<skill>/SKILL.md
        # or directly: <category>/<skill>/SKILL.md
        repo_skills_root = _find_skills_root(CACHE_DIR)
        if not repo_skills_root:
            msg = "Could not find skills directory structure in repo"
            logger.warning("SKILLS_SYNC: %s", msg)
            _last_sync = {"timestamp": _now(), "status": "error", "added": 0, "updated": 0, "error": msg}
            return {"status": "error", "added": 0, "updated": 0, "details": msg}

        # Step 3: Copy skills to data dir
        added, updated = _copy_skills(repo_skills_root, SKILLS_DATA_DIR)

        msg = f"Synced: {added} added, {updated} updated"
        logger.info("SKILLS_SYNC: %s", msg)
        _last_sync = {"timestamp": _now(), "status": "ok", "added": added, "updated": updated, "error": None}
        return {"status": "ok", "added": added, "updated": updated, "details": msg}

    except subprocess.TimeoutExpired:
        msg = "Git operation timed out (60s)"
        logger.error("SKILLS_SYNC: %s", msg)
        _last_sync = {"timestamp": _now(), "status": "error", "added": 0, "updated": 0, "error": msg}
        return {"status": "error", "added": 0, "updated": 0, "details": msg}
    except Exception as exc:
        msg = str(exc)
        logger.exception("SKILLS_SYNC: failed")
        _last_sync = {"timestamp": _now(), "status": "error", "added": 0, "updated": 0, "error": msg}
        return {"status": "error", "added": 0, "updated": 0, "details": msg}


def _clone_repo():
    logger.info("SKILLS_SYNC: cloning %s", REPO_URL)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth=1", REPO_URL, str(CACHE_DIR)],
        capture_output=True, text=True, timeout=60, check=True,
    )


def _find_skills_root(repo_dir: Path) -> Path | None:
    """
    Find the root directory containing skill categories.
    Looks for a directory that contains subdirs with SKILL.md files.
    """
    # Try common patterns
    for candidate in [repo_dir / "skills", repo_dir]:
        if not candidate.is_dir():
            continue
        # Check if any subdir/subdir/SKILL.md exists
        for cat_dir in candidate.iterdir():
            if not cat_dir.is_dir():
                continue
            for skill_dir in cat_dir.iterdir():
                if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                    return candidate
    return None


def _copy_skills(src_root: Path, dst_root: Path) -> tuple[int, int]:
    """
    Copy skill directories from src to dst.
    Returns (added_count, updated_count).
    """
    added = 0
    updated = 0

    for cat_dir in sorted(src_root.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith("."):
            continue

        dst_cat = dst_root / cat_dir.name
        dst_cat.mkdir(parents=True, exist_ok=True)

        for skill_dir in sorted(cat_dir.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            if not (skill_dir / "SKILL.md").exists():
                continue

            dst_skill = dst_cat / skill_dir.name
            is_new = not dst_skill.exists()

            # Check if content changed (compare SKILL.md)
            if not is_new:
                try:
                    old_content = (dst_skill / "SKILL.md").read_text(encoding="utf-8")
                    new_content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
                    if old_content == new_content:
                        continue  # No change
                except Exception:
                    pass  # Can't compare, just copy

            # Copy the entire skill directory
            if dst_skill.exists():
                shutil.rmtree(dst_skill)
            shutil.copytree(skill_dir, dst_skill)

            if is_new:
                added += 1
                logger.info("SKILLS_SYNC: added %s/%s", cat_dir.name, skill_dir.name)
            else:
                updated += 1
                logger.info("SKILLS_SYNC: updated %s/%s", cat_dir.name, skill_dir.name)

    return added, updated


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def start_periodic_sync(skills_library, interval_hours: int = 6):
    """
    Background task: sync skills every N hours and reload the library.
    """
    logger.info("SKILLS_SYNC: periodic sync started (every %dh)", interval_hours)
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            result = sync_skills()
            if result["status"] == "ok" and (result["added"] > 0 or result["updated"] > 0):
                skills_library.reload()
                logger.info("SKILLS_SYNC: library reloaded after sync (%d added, %d updated)",
                            result["added"], result["updated"])
            else:
                logger.info("SKILLS_SYNC: no changes detected")
        except asyncio.CancelledError:
            logger.info("SKILLS_SYNC: periodic sync stopped")
            return
        except Exception:
            logger.exception("SKILLS_SYNC: periodic sync error")
