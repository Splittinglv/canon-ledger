#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
记忆契约类型与 Protocol 定义。

上层消费者（context-agent、data-agent、reviewer）只依赖本模块的类型和协议，
不直接依赖 StateManager / IndexManager / ScratchpadManager 等具体实现。

具体实现见 memory_contract_adapter.py。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# 契约返回类型
# ---------------------------------------------------------------------------

@dataclass
class CommitResult:
    """commit_chapter 的返回值。"""
    chapter: int
    entities_added: int = 0
    entities_updated: int = 0
    state_changes_recorded: int = 0
    relationships_added: int = 0
    memory_items_added: int = 0
    summary_path: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EntitySnapshot:
    """query_entity 的返回值。"""
    id: str
    name: str
    type: str = "角色"
    tier: str = "核心"
    aliases: List[str] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)
    first_appearance: int = 0
    last_appearance: int = 0
    recent_state_changes: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Rule:
    """query_rules 的返回值项。"""
    id: str
    subject: str
    field: str
    value: str
    domain: str = ""
    source_chapter: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OpenLoop:
    """get_open_loops 的返回值项。"""
    id: str
    content: str
    status: str = "active"
    planted_chapter: int = 0
    expected_payoff: str = ""
    urgency: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TimelineEvent:
    """get_timeline 的返回值项。"""
    event: str
    chapter: int = 0
    time_hint: str = ""
    event_type: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LifecycleObligation:
    """A stable, explicitly targetable open loop or reader promise."""
    id: str
    category: str
    content: str
    status: str = "active"
    source_chapter: int = 0
    expected_payoff: str = ""
    urgency: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ContextPack:
    """load_context 的返回值。sections 由调用者解释具体结构。"""
    chapter: int
    sections: Dict[str, Any] = field(default_factory=dict)
    budget_used_tokens: int = 0
    schema_version: str = "canon-ledger-context-pack/v2"
    budget: Dict[str, Any] = field(default_factory=dict)
    completeness: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 契约 Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class MemoryContract(Protocol):
    """记忆模块统一契约。

    上层消费者依赖此 Protocol，不依赖具体实现类。
    """

    def commit_chapter(self, chapter: int, result: dict) -> CommitResult:
        """写后提交：将章节处理结果写入所有存储。"""
        ...

    def load_context(self, chapter: int, budget_tokens: int = 4000) -> ContextPack:
        """写前读取：加载章节上下文包。"""
        ...

    def query_entity(
        self, entity_id: str, as_of_chapter: int | None = None
    ) -> Optional[EntitySnapshot]:
        """查询单个实体快照；历史写作必须传入截止章节。"""
        ...

    def query_rules(
        self, domain: str = "", as_of_chapter: int | None = None
    ) -> List[Rule]:
        """查询世界规则，可按 domain 和截止章节过滤。"""
        ...

    def read_summary(self, chapter: int) -> str:
        """读取章节摘要文本。"""
        ...

    def get_open_loops(
        self, status: str = "active", as_of_chapter: int | None = None
    ) -> List[OpenLoop]:
        """查询截止指定章节仍未闭合的伏笔/悬念。"""
        ...

    def get_lifecycle_obligations(
        self, status: str = "active", as_of_chapter: int | None = None
    ) -> List[LifecycleObligation]:
        """查询可由 close/payoff 事件明确引用的稳定目标。"""
        ...

    def get_timeline(
        self, from_ch: int, to_ch: int, as_of_chapter: int | None = None
    ) -> List[TimelineEvent]:
        """查询章节范围内的时间线事件。"""
        ...

    def export_asof_snapshot(
        self, chapter: int | None = None, as_of_chapter: int | None = None
    ) -> Dict[str, Any]:
        """导出截至 N-1 的不可变事实快照，供 reviewer / data-agent 使用。"""
        ...
