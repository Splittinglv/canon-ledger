#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当前章节提交投影测试。

测试数据只使用 data-agent 公开的规范字段，并通过
``extraction_result`` 进入五路投影。
"""

import json

from data_modules.config import DataModulesConfig
from data_modules.chapter_content_binding import build_chapter_binding
from data_modules.memory.store import ScratchpadManager
from data_modules.memory_projection_writer import MemoryProjectionWriter
from data_modules.state_projection_writer import StateProjectionWriter
from data_modules.vector_projection_writer import VectorProjectionWriter


_EXTRACTION_FIELDS = (
    "accepted_events",
    "state_deltas",
    "entity_deltas",
    "entities_appeared",
    "scenes",
    "timeline_events",
    "summary_text",
)


def _project(writer, payload: dict):
    """把规范提取产物组装为当前提交后执行投影。"""
    extraction = {
        field: payload[field]
        for field in _EXTRACTION_FIELDS
        if field in payload
    }
    commit = {
        "meta": dict(payload.get("meta") or {}),
        "extraction_result": extraction,
    }
    if "chapter_binding" in payload:
        commit["chapter_binding"] = payload["chapter_binding"]
    return writer.apply(commit)


# ============================================================
# state_projection_writer：投影规范状态变更
# ============================================================


def test_state_writer_projects_nested_current_field(tmp_path):
    (tmp_path / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    writer = StateProjectionWriter(tmp_path)

    _project(writer,
        {
            "meta": {"status": "accepted", "chapter": 2},
            "state_deltas": [
                {
                    "entity_id": "luming",
                    "field": "physical.condition",
                    "old": "虚弱",
                    "new": "虚弱（持续）",
                    "change_type": "confirmed",
                }
            ],
        }
    )

    payload = json.loads((tmp_path / ".canon-ledger" / "state.json").read_text(encoding="utf-8"))
    luming = payload["entity_state"]["luming"]
    # 嵌套路径展开成字典
    assert luming["physical"]["condition"] == "虚弱（持续）"


def test_state_writer_projects_current_field(tmp_path):
    """规范的 field/new 字段会写入状态读模型。"""
    (tmp_path / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    writer = StateProjectionWriter(tmp_path)

    _project(writer,
        {
            "meta": {"status": "accepted", "chapter": 3},
            "state_deltas": [{"entity_id": "x", "field": "realm", "new": "斗师"}],
        }
    )

    payload = json.loads((tmp_path / ".canon-ledger" / "state.json").read_text(encoding="utf-8"))
    assert payload["entity_state"]["x"]["realm"] == "斗师"


def test_state_writer_handles_array_value_in_nested_field(tmp_path):
    (tmp_path / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    writer = StateProjectionWriter(tmp_path)

    _project(writer,
        {
            "meta": {"status": "accepted", "chapter": 2},
            "state_deltas": [
                {
                    "entity_id": "luming",
                    "field": "relationships.acquaintances",
                    "old": [],
                    "new": [
                        {"entity_id": "liu_dazhu", "type": "同屋杂役"},
                        {"entity_id": "sun_wang", "type": "同屋杂役"},
                    ],
                    "change_type": "initialize",
                }
            ],
        }
    )

    payload = json.loads((tmp_path / ".canon-ledger" / "state.json").read_text(encoding="utf-8"))
    acquaintances = payload["entity_state"]["luming"]["relationships"]["acquaintances"]
    assert len(acquaintances) == 2
    assert acquaintances[0]["entity_id"] == "liu_dazhu"


def test_state_writer_mirrors_protagonist_state_when_entity_is_protagonist(tmp_path):
    """主角实体的状态变更应同步到主角状态读模型。"""
    (tmp_path / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    initial = {
        "protagonist_state": {
            "name": "陆鸣",
            "power": {"realm": "", "layer": 1},
            "location": {"current": "", "last_chapter": 0},
            "golden_finger": {"name": "穿越者知识", "level": 1, "cooldown": 0, "skills": []},
            "attributes": {},
        }
    }
    (tmp_path / ".canon-ledger" / "state.json").write_text(json.dumps(initial), encoding="utf-8")
    writer = StateProjectionWriter(tmp_path)

    _project(writer,
        {
            "meta": {"status": "accepted", "chapter": 2},
            "state_deltas": [
                {"entity_id": "luming", "field": "power.realm", "new": "练气五层"},
                {
                    "entity_id": "luming",
                    "field": "location.current",
                    "new": "青云宗杂役院",
                },
            ],
            "entity_deltas": [
                {
                    "entity_id": "luming",
                    "canonical_name": "陆鸣",
                    "entity_type": "角色",
                    "tier": "核心",
                    "is_protagonist": True,
                }
            ],
        }
    )

    payload = json.loads((tmp_path / ".canon-ledger" / "state.json").read_text(encoding="utf-8"))
    assert payload["protagonist_state"]["power"]["realm"] == "练气五层"
    assert payload["protagonist_state"]["location"]["current"] == "青云宗杂役院"
    # name 不应被 delta 写回覆盖
    assert payload["protagonist_state"]["name"] == "陆鸣"


def test_state_writer_recognizes_protagonist_via_tier_zhujue(tmp_path):
    """实际 LLM 用 tier='主角' 标注，而不是 is_protagonist=True。"""
    (tmp_path / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    initial = {
        "protagonist_state": {
            "name": "陆鸣",
            "power": {"realm": "", "layer": 1},
        }
    }
    (tmp_path / ".canon-ledger" / "state.json").write_text(json.dumps(initial), encoding="utf-8")
    writer = StateProjectionWriter(tmp_path)

    _project(writer,
        {
            "meta": {"status": "accepted", "chapter": 1},
            "state_deltas": [
                {"entity_id": "luming", "field": "power.realm", "new": "练气五层"},
            ],
            "entity_deltas": [
                {
                    "entity_id": "luming",
                    "canonical_name": "陆鸣",
                    "entity_type": "角色",
                    "tier": "主角",
                }
            ],
        }
    )

    payload = json.loads((tmp_path / ".canon-ledger" / "state.json").read_text(encoding="utf-8"))
    assert payload["protagonist_state"]["power"]["realm"] == "练气五层"


def test_state_writer_recognizes_protagonist_via_canonical_name_match(tmp_path):
    """没有 tier 也没有 is_protagonist 时，按名字匹配 state.protagonist_state.name 兜底。"""
    (tmp_path / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    initial = {"protagonist_state": {"name": "陆鸣", "power": {}}}
    (tmp_path / ".canon-ledger" / "state.json").write_text(json.dumps(initial), encoding="utf-8")
    writer = StateProjectionWriter(tmp_path)

    _project(writer,
        {
            "meta": {"status": "accepted", "chapter": 1},
            "state_deltas": [
                {"entity_id": "luming", "field": "power.realm", "new": "练气五层"},
            ],
            "entity_deltas": [
                {"entity_id": "luming", "canonical_name": "陆鸣", "entity_type": "角色"}
            ],
        }
    )

    payload = json.loads((tmp_path / ".canon-ledger" / "state.json").read_text(encoding="utf-8"))
    assert payload["protagonist_state"]["power"]["realm"] == "练气五层"


# ============================================================
# index_manager.apply_entity_delta：tier=主角 / entity_type 识别
# ============================================================


def test_index_manager_marks_protagonist_via_tier_zhujue(tmp_path):
    """tier='主角' 时应自动设置 is_protagonist=True，让 get_protagonist 找得到。"""
    from data_modules.index_manager import IndexManager

    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    manager = IndexManager(cfg)

    manager.apply_entity_delta(
        {
            "entity_id": "luming",
            "action": "upsert",
            "entity_type": "角色",
            "tier": "主角",
            "chapter": 1,
            "payload": {"name": "陆鸣"},
        }
    )

    protagonist = manager.get_protagonist()
    assert protagonist is not None, "主角层级应被识别为主角"
    assert protagonist["id"] == "luming"


def test_index_manager_preserves_entity_type_for_organization(tmp_path):
    """entity_deltas 用 entity_type='组织' 时，索引里 type 也必须是 '组织' 而非默认 '角色'。"""
    from data_modules.index_manager import IndexManager

    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    manager = IndexManager(cfg)

    manager.apply_entity_delta(
        {
            "entity_id": "qingyun_zong",
            "action": "upsert",
            "entity_type": "组织",
            "tier": "重要",
            "chapter": 1,
            "payload": {"name": "青云宗"},
        }
    )

    entity = manager.get_entity("qingyun_zong")
    assert entity is not None
    assert entity["type"] == "组织", f"实体类型应为组织，实际为 {entity['type']!r}"


def test_index_manager_uses_payload_name_when_canonical_name_missing(tmp_path):
    """LLM 实际输出常把名字放在 payload.name，而非顶层 canonical_name。"""
    from data_modules.index_manager import IndexManager

    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    manager = IndexManager(cfg)

    manager.apply_entity_delta(
        {
            "entity_id": "lu_ming",
            "action": "upsert",
            "entity_type": "角色",
            "tier": "主角",
            "chapter": 1,
            "payload": {"name": "陆鸣"},
        }
    )

    entity = manager.get_entity("lu_ming")
    assert entity is not None
    assert entity["canonical_name"] == "陆鸣", (
        f"规范名应为陆鸣，实际为 {entity['canonical_name']!r}"
    )


def test_index_manager_updates_entity_type_on_reprojection(tmp_path):
    """同一实体重新投影时应使用提交中明确的实体类型。"""
    from data_modules.index_manager import IndexManager

    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    manager = IndexManager(cfg)

    # 首次登记为角色。
    manager.apply_entity_delta(
        {
            "entity_id": "qingyun_zong",
            "tier": "重要",
            "chapter": 1,
            "payload": {"name": "青云宗"},
        }
    )
    entity = manager.get_entity("qingyun_zong")
    assert entity["type"] == "角色"

    # 后续提交明确更正为组织。
    manager.apply_entity_delta(
        {
            "entity_id": "qingyun_zong",
            "entity_type": "组织",
            "tier": "重要",
            "chapter": 1,
            "payload": {"name": "青云宗"},
        }
    )
    entity = manager.get_entity("qingyun_zong")
    assert entity["type"] == "组织", f"重新投影后实体类型应为组织，实际为 {entity['type']!r}"


def test_index_manager_resolves_underscored_id_to_compact_entity(tmp_path):
    """实体登记为 'luming' 时，查询 'lu_ming' 也应能找到（LLM 命名风格不一致兜底）。"""
    from data_modules.index_manager import IndexManager

    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    manager = IndexManager(cfg)

    manager.apply_entity_delta(
        {
            "entity_id": "luming",
            "entity_type": "角色",
            "tier": "主角",
            "chapter": 1,
            "payload": {"name": "陆鸣"},
        }
    )

    # 直接查 — 已经能工作
    assert manager.get_entity("luming")["id"] == "luming"
    # 反向兜底 — 带下划线变体
    found = manager.get_entity("lu_ming")
    assert found is not None, "带下划线的实体标识应解析到已登记实体"
    assert found["id"] == "luming"


# ============================================================
# memory_writer：投影规范实体、状态与伏笔事件
# ============================================================


def test_memory_writer_preserves_entity_type_for_organization(tmp_path):
    """entity_deltas 用 entity_type 字段时，组织不能被误标为'角色'。"""
    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    writer = MemoryProjectionWriter(tmp_path)

    _project(writer,
        {
            "meta": {"status": "accepted", "chapter": 1},
            "entity_deltas": [
                {
                    "entity_id": "qingyun_zong",
                    "action": "upsert",
                    "entity_type": "组织",
                    "tier": "重要",
                    "payload": {"name": "青云宗"},
                }
            ],
            "state_deltas": [],
            "accepted_events": [],
        }
    )

    store = ScratchpadManager(cfg)
    chars = store.query(category="character_state", status="active")
    qingyun = [x for x in chars if x.subject == "qingyun_zong"]
    assert qingyun, "组织实体应写入记忆读模型"
    assert qingyun[0].payload.get("type") == "组织", (
        f"实体类型应为组织，实际为 {qingyun[0].payload.get('type')!r}"
    )


def test_memory_writer_projects_nested_current_state_delta(tmp_path):
    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    writer = MemoryProjectionWriter(tmp_path)

    _project(writer,
        {
            "meta": {"status": "accepted", "chapter": 2},
            "state_deltas": [
                {
                    "entity_id": "luming",
                    "field": "physical.condition",
                    "old": "虚弱",
                    "new": "虚弱（持续）",
                }
            ],
            "entity_deltas": [],
            "accepted_events": [],
        }
    )

    store = ScratchpadManager(cfg)
    chars = store.query(category="character_state", status="active")
    assert any(
        x.subject == "luming" and "physical" in x.field for x in chars
    ), [(x.subject, x.field) for x in chars]


def test_memory_writer_projects_open_loop_content(tmp_path):
    """伏笔创建事件应使用规范标识与内容。"""
    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    writer = MemoryProjectionWriter(tmp_path)

    _project(writer,
        {
            "meta": {"status": "accepted", "chapter": 2},
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [
                {
                    "event_id": "evt_002",
                    "chapter": 2,
                    "event_type": "open_loop_created",
                    "subject": "luming",
                    "payload": {
                        "loop_id": "loop-guarantor",
                        "content": "陆鸣发现借据'一式三份'条款，保人身份成谜",
                        "loop_type": "身份悬疑",
                        "unanswered_question": "保人是谁？谁带原身去签的借据？",
                        "narrative_weight": "major",
                    },
                }
            ],
        }
    )

    store = ScratchpadManager(cfg)
    loops = store.query(category="open_loop", status="active")
    assert loops, "伏笔事件应写入记忆读模型"
    # 伏笔主题应保留规范 content 中的悬念内容。
    contents = [x.subject for x in loops]
    assert any("保人" in c or "借据" in c or "身份" in c for c in contents), (
        f"应保留有意义的伏笔内容，实际为 {contents}"
    )


def test_memory_writer_extracts_world_rule_from_rule_content(tmp_path):
    """世界规则事件的规范内容应落入世界规则记忆。"""
    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    writer = MemoryProjectionWriter(tmp_path)
    chapter_path = tmp_path / "正文" / "第0002章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_quote = "青云城借贷市场：利率垄断、无信用体系、暴力收债"
    chapter_path.write_text(evidence_quote, encoding="utf-8")
    binding = build_chapter_binding(tmp_path, 2)

    _project(writer,
        {
            "meta": {"status": "accepted", "chapter": 2},
            "chapter_binding": binding,
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [
                {
                    "event_id": "evt_003",
                    "chapter": 2,
                    "event_type": "world_rule_revealed",
                    "subject": "青云城借贷市场",
                    "payload": {
                        "rule_category": "经济",
                        "domain": "青云城借贷市场",
                        "field": "借贷秩序",
                        "rule_content": "利率垄断、无信用体系、暴力收债",
                        "evidence_quote": evidence_quote,
                    },
                }
            ],
        }
    )

    store = ScratchpadManager(cfg)
    rules = store.query(category="world_rule", status="active")
    assert rules
    rule_texts = [x.value for x in rules]
    assert any("利率" in r or "信用" in r or "金融" in r or "借贷" in r for r in rule_texts), (
        f"应保留有意义的规则内容，实际为 {rule_texts}"
    )


# ============================================================
# vector_projection_writer：将规范事件转换为检索文本
# ============================================================


def test_vector_writer_handles_current_character_state_event():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    event = {
        "event_type": "character_state_changed",
        "chapter": 2,
        "subject": "luming",
        "payload": {
            "field": "knowledge.finance",
            "old": "刚穿越的茫然",
            "new": "认知激活",
        },
    }
    text = writer._event_to_text(event)
    assert text, "规范事件应产生非空检索文本"
    assert "第2章" in text
    assert "luming" in text or "陆鸣" in text or "认知" in text or "金融" in text


# ============================================================
# 集成：用当前规范提交走完整投影链
# ============================================================


def test_integration_current_commit_projects_full_state(tmp_path):
    """端到端确认当前提交的状态与记忆会落到对应读模型。"""
    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    (tmp_path / ".canon-ledger" / "state.json").write_text(
        json.dumps(
            {
                "protagonist_state": {
                    "name": "陆鸣",
                    "power": {"realm": "", "layer": 1},
                    "location": {"current": ""},
                }
            }
        ),
        encoding="utf-8",
    )

    # 当前 data-agent 规范提取产物。
    real_payload = {
        "meta": {"status": "accepted", "chapter": 2},
        "state_deltas": [
            {
                "entity_id": "luming",
                "field": "physical.condition",
                "old": "虚弱",
                "new": "虚弱（持续）",
                "change_type": "confirmed",
            },
            {
                "entity_id": "luming",
                "field": "knowledge.lending_ecosystem",
                "old": "仅知有阎王债",
                "new": "完整市场图谱",
                "change_type": "initialize",
            },
        ],
        "entity_deltas": [
            {
                "entity_id": "luming",
                "action": "upsert",
                "entity_type": "角色",
                "tier": "核心",
                "is_protagonist": True,
                "payload": {"name": "陆鸣"},
            },
            {
                "entity_id": "qingyun_zong",
                "action": "upsert",
                "entity_type": "组织",
                "tier": "重要",
                "payload": {"name": "青云宗"},
            },
            {
                "entity_id": "heishi_fangshi",
                "action": "upsert",
                "entity_type": "地点",
                "tier": "重要",
                "payload": {"name": "黑石坊市"},
            },
        ],
        "accepted_events": [
            {
                "event_id": "evt_ch002_guarantor_mystery",
                "chapter": 2,
                "event_type": "open_loop_created",
                "subject": "luming",
                "payload": {
                    "loop_id": "loop-guarantor",
                    "content": "保人身份不明",
                    "loop_type": "身份悬疑",
                    "unanswered_question": "保人是谁？",
                },
            }
        ],
    }

    _project(StateProjectionWriter(tmp_path), real_payload)
    _project(MemoryProjectionWriter(tmp_path), real_payload)

    state = json.loads((tmp_path / ".canon-ledger" / "state.json").read_text(encoding="utf-8"))
    # entity_state 必须有内容（不能再是 {}）
    assert state["entity_state"], "实体状态读模型不应为空"
    assert "luming" in state["entity_state"]
    # 主角字段镜像到 protagonist_state 不丢
    assert state["protagonist_state"]["name"] == "陆鸣"

    # memory_scratchpad：组织和地点不能被误标
    store = ScratchpadManager(cfg)
    chars = store.query(category="character_state", status="active")
    by_id = {x.subject: x for x in chars}
    assert by_id["qingyun_zong"].payload.get("type") == "组织"
    assert by_id["heishi_fangshi"].payload.get("type") == "地点"

    # open_loop 必须有有意义内容（不能是 'luming'）
    loops = store.query(category="open_loop", status="active")
    contents = [x.subject for x in loops]
    assert any("保人" in c or "身份悬疑" in c for c in contents), contents
