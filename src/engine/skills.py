import datetime
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import yaml

logger = logging.getLogger(__name__)


class SkillItem:
    """Represents a Skill's metadata and state."""

    def __init__(
        self,
        id: str,
        name: str,
        description: str,
        enabled: bool = True,
        content: str = "",
        directory_path: Optional[str] = None,
        updated_at: Optional[str] = None,
    ):
        self.id = id
        self.name = name
        self.description = description
        self.enabled = enabled
        self.content = content
        self.directory_path = directory_path
        self.updated_at = updated_at or datetime.datetime.now(datetime.timezone.utc).isoformat()

    def to_dict(self, include_content: bool = False) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "directory_path": self.directory_path,
            "updated_at": self.updated_at,
        }
        if include_content:
            data["content"] = self.content
        return data


class SkillEngine:
    """Manages skill definitions stored in directories with SKILL.md files,
    tracks enabled state in skills_config.yaml, and synchronizes with GCS."""

    def __init__(self, memory_dir: str, gcs_manager: Optional[Any] = None, seed_dir: Any = "DEFAULT"):
        self.memory_dir = Path(memory_dir)
        self.skills_dir = self.memory_dir / "skills"
        self.config_file = self.memory_dir / "skills_config.yaml"
        self.gcs_manager = gcs_manager
        if seed_dir == "DEFAULT":
            self.seed_dir = Path(".agents/skills")
        elif seed_dir:
            self.seed_dir = Path(seed_dir)
        else:
            self.seed_dir = None
        self._skills: Dict[str, SkillItem] = {}
        self._load()

    @staticmethod
    def sanitize_id(name: str) -> str:
        """Sanitizes skill name into a valid directory/id slug."""
        clean = re.sub(r"[^a-zA-Z0-9_\-]+", "-", name.lower().strip())
        clean = clean.strip("-")
        return clean or "custom-skill"

    @staticmethod
    def parse_skill_md(file_path: Path) -> tuple[str, str, str]:
        """Parses a SKILL.md file returning (name, description, markdown_body)."""
        if not file_path.exists():
            return "", "", ""

        raw_text = file_path.read_text(encoding="utf-8")
        if raw_text.startswith("---"):
            parts = raw_text.split("---", 2)
            if len(parts) >= 3:
                frontmatter_str = parts[1]
                body = parts[2].strip()
                try:
                    meta = yaml.safe_load(frontmatter_str) or {}
                    name = str(meta.get("name", file_path.parent.name))
                    desc = str(meta.get("description", ""))
                    return name, desc, body
                except Exception as e:
                    logger.warning(f"Error parsing frontmatter from {file_path}: {e}")
                    return file_path.parent.name, "", body

        # If no frontmatter, extract first heading as name
        lines = raw_text.splitlines()
        name = file_path.parent.name
        desc = ""
        body = raw_text.strip()
        for line in lines:
            if line.startswith("# "):
                name = line[2:].strip()
                break
        return name, desc, body

    @staticmethod
    def format_skill_md(name: str, description: str, body: str) -> str:
        """Generates SKILL.md formatted text with YAML frontmatter."""
        clean_desc = description.strip().replace('"', '\\"')
        header = f"---\nname: {name.strip()}\ndescription: \"{clean_desc}\"\n---\n\n"
        return header + body.strip() + "\n"

    def _load_config(self) -> Dict[str, Dict[str, Any]]:
        """Loads enabled states and metadata overrides from skills_config.yaml."""
        if not self.config_file.exists():
            return {}
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                return data.get("skills", {})
        except Exception as e:
            logger.warning(f"Failed to read {self.config_file}: {e}")
            return {}

    def _save_config(self) -> None:
        """Saves enabled states to skills_config.yaml and syncs to GCS."""
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            skills_meta = {}
            for s_id, item in self._skills.items():
                skills_meta[s_id] = {
                    "enabled": item.enabled,
                    "updated_at": item.updated_at,
                }
            data = {"skills": skills_meta}
            with open(self.config_file, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

            if self.gcs_manager:
                self.gcs_manager.sync_to_gcs()
        except Exception as e:
            logger.error(f"Failed to save skills config: {e}")

    def _seed_initial_skills(self) -> None:
        """Seeds default skills from repository seed_dir if local skills_dir is empty."""
        if self.skills_dir.exists() and any(self.skills_dir.iterdir()):
            return

        self.skills_dir.mkdir(parents=True, exist_ok=True)
        if self.seed_dir and self.seed_dir.exists() and self.seed_dir.is_dir():
            for child in self.seed_dir.iterdir():
                if child.is_dir() and (child / "SKILL.md").exists():
                    dest = self.skills_dir / child.name
                    dest.mkdir(parents=True, exist_ok=True)
                    skill_content = (child / "SKILL.md").read_text(encoding="utf-8")
                    (dest / "SKILL.md").write_text(skill_content, encoding="utf-8")
                    logger.info(f"Seeded skill '{child.name}' into {dest}")

    def _load(self) -> None:
        """Scans skills directory, loads SKILL.md files, and correlates with config."""
        self._seed_initial_skills()
        config_map = self._load_config()
        self._skills = {}

        if not self.skills_dir.exists():
            return

        for folder in self.skills_dir.iterdir():
            if folder.is_dir():
                skill_file = folder / "SKILL.md"
                if skill_file.exists():
                    s_id = folder.name
                    name, desc, body = self.parse_skill_md(skill_file)
                    cfg = config_map.get(s_id, {})
                    enabled = cfg.get("enabled", True)
                    updated_at = cfg.get("updated_at")

                    self._skills[s_id] = SkillItem(
                        id=s_id,
                        name=name or s_id,
                        description=desc,
                        enabled=enabled,
                        content=skill_file.read_text(encoding="utf-8"),
                        directory_path=str(folder.resolve()),
                        updated_at=updated_at,
                    )

    def list_skills(self) -> List[Dict[str, Any]]:
        """Returns list of all skills metadata."""
        return [item.to_dict(include_content=False) for item in sorted(self._skills.values(), key=lambda s: s.name.lower())]

    def get_skill(self, skill_id: str) -> Optional[SkillItem]:
        """Returns skill item including full SKILL.md content."""
        return self._skills.get(skill_id)

    def create_skill(self, name: str, description: str, content: str, enabled: bool = True) -> SkillItem:
        """Creates a new skill directory with SKILL.md."""
        skill_id = self.sanitize_id(name)
        folder = self.skills_dir / skill_id
        folder.mkdir(parents=True, exist_ok=True)

        # Ensure content has frontmatter
        if not content.strip().startswith("---"):
            full_content = self.format_skill_md(name, description, content)
        else:
            full_content = content

        skill_file = folder / "SKILL.md"
        skill_file.write_text(full_content, encoding="utf-8")

        item = SkillItem(
            id=skill_id,
            name=name,
            description=description,
            enabled=enabled,
            content=full_content,
            directory_path=str(folder.resolve()),
            updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        self._skills[skill_id] = item
        self._save_config()
        logger.info(f"Created skill '{name}' ({skill_id})")
        return item

    def update_skill(
        self,
        skill_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Optional[SkillItem]:
        """Updates an existing skill's metadata and/or content."""
        item = self._skills.get(skill_id)
        if not item:
            return None

        folder = self.skills_dir / skill_id
        folder.mkdir(parents=True, exist_ok=True)
        skill_file = folder / "SKILL.md"

        if name is not None:
            item.name = name
        if description is not None:
            item.description = description
        if enabled is not None:
            item.enabled = enabled

        if content is not None:
            if not content.strip().startswith("---"):
                full_content = self.format_skill_md(item.name, item.description, content)
            else:
                full_content = content
            item.content = full_content
            skill_file.write_text(full_content, encoding="utf-8")
        elif name is not None or description is not None:
            _, _, body = self.parse_skill_md(skill_file)
            full_content = self.format_skill_md(item.name, item.description, body)
            item.content = full_content
            skill_file.write_text(full_content, encoding="utf-8")

        item.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._save_config()
        logger.info(f"Updated skill '{skill_id}'")
        return item

    def toggle_skill(self, skill_id: str, enabled: bool) -> Optional[SkillItem]:
        """Toggles the enabled status of a skill."""
        item = self._skills.get(skill_id)
        if not item:
            return None
        item.enabled = enabled
        item.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._save_config()
        logger.info(f"Toggled skill '{skill_id}' enabled={enabled}")
        return item

    def delete_skill(self, skill_id: str) -> bool:
        """Deletes a skill directory and unregisters it."""
        if skill_id not in self._skills:
            return False

        folder = self.skills_dir / skill_id
        if folder.exists():
            import shutil
            shutil.rmtree(folder, ignore_errors=True)

        del self._skills[skill_id]
        self._save_config()
        logger.info(f"Deleted skill '{skill_id}'")
        return True

    def get_active_skills_paths(self) -> List[str]:
        """Returns list of directory paths for all currently enabled skills."""
        paths = []
        for item in self._skills.values():
            if item.enabled and item.directory_path and Path(item.directory_path).exists():
                paths.append(item.directory_path)
        return paths

    def get_active_skills_summary(self) -> str:
        """Returns a formatted summary of active skills to inject into prompt/system instructions."""
        active = [item for item in self._skills.values() if item.enabled]
        if not active:
            return ""

        lines = ["ACTIVE CAPABILITIES & SPECIALIZED SKILLS:"]
        for it in active:
            lines.append(f"- **{it.name}** (`{it.id}`): {it.description or 'No description provided.'}")
        return "\n".join(lines)
