import os
from pathlib import Path
import pytest
from src.engine.skills import SkillEngine, SkillItem
from src.engine.agent import SyntheticIdentityAgent


def test_skill_engine_crud(tmp_path):
    memory_dir = tmp_path / "brain"
    engine = SkillEngine(memory_dir=str(memory_dir), seed_dir=None)

    # 1. Create skill
    content = "# Customer Support Guide\n\nAlways greet warmly."
    skill = engine.create_skill(
        name="Customer Support",
        description="Handles customer inquiries with empathy",
        content=content,
        enabled=True,
    )

    assert skill.id == "customer-support"
    assert skill.enabled is True
    assert (memory_dir / "skills" / "customer-support" / "SKILL.md").exists()
    assert (memory_dir / "skills_config.yaml").exists()

    # 2. List skills
    skills_list = engine.list_skills()
    assert len(skills_list) == 1
    assert skills_list[0]["id"] == "customer-support"
    assert skills_list[0]["enabled"] is True

    # 3. Get skill detail
    fetched = engine.get_skill("customer-support")
    assert fetched is not None
    assert fetched.name == "Customer Support"
    assert "Handles customer inquiries" in fetched.description
    assert "Always greet warmly" in fetched.content

    # 4. Update skill
    updated = engine.update_skill(
        skill_id="customer-support",
        name="Customer Support Pro",
        description="Advanced customer empathy guidelines",
    )
    assert updated.name == "Customer Support Pro"
    assert updated.description == "Advanced customer empathy guidelines"

    # 5. Toggle skill
    toggled = engine.toggle_skill("customer-support", enabled=False)
    assert toggled.enabled is False
    assert len(engine.get_active_skills_paths()) == 0

    engine.toggle_skill("customer-support", enabled=True)
    active_paths = engine.get_active_skills_paths()
    assert len(active_paths) == 1
    assert "customer-support" in active_paths[0]

    # Active summary
    summary = engine.get_active_skills_summary()
    assert "ACTIVE CAPABILITIES & SPECIALIZED SKILLS" in summary
    assert "Customer Support Pro" in summary

    # 6. Delete skill
    deleted = engine.delete_skill("customer-support")
    assert deleted is True
    assert engine.get_skill("customer-support") is None
    assert not (memory_dir / "skills" / "customer-support").exists()
    assert len(engine.list_skills()) == 0


def test_skill_engine_reloads_persisted_state(tmp_path):
    memory_dir = tmp_path / "brain"
    engine1 = SkillEngine(memory_dir=str(memory_dir), seed_dir=None)
    engine1.create_skill(
        name="Calendar Helper",
        description="Helps triage calendar meetings",
        content="Guide for calendar",
        enabled=False,
    )

    # Initialize second instance over same memory directory
    engine2 = SkillEngine(memory_dir=str(memory_dir), seed_dir=None)
    assert len(engine2.list_skills()) == 1
    s = engine2.get_skill("calendar-helper")
    assert s is not None
    assert s.enabled is False
    assert s.description == "Helps triage calendar meetings"


def test_agent_includes_active_skills(tmp_path):
    memory_dir = tmp_path / "brain"
    engine = SkillEngine(memory_dir=str(memory_dir), seed_dir=None)
    engine.create_skill(
        name="VIP Protocol",
        description="Special protocol for executive VIP messages",
        content="Prioritize speed and polite phrasing.",
        enabled=True,
    )

    agent = SyntheticIdentityAgent(
        app_data_dir=str(memory_dir),
        skills_engine=engine,
    )

    instructions = agent._build_system_instructions(service="chat")
    assert "ACTIVE CAPABILITIES & SPECIALIZED SKILLS:" in instructions
    assert "VIP Protocol" in instructions
    assert "Special protocol for executive VIP messages" in instructions
