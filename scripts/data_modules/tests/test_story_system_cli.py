#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import hashlib
import json
import sys

import pytest


def _write_csv(path, headers, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_story_system_persist_writes_master_chapter_and_anti_patterns(tmp_path, monkeypatch):
    project_root = tmp_path / "book"
    (project_root / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (project_root / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")

    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    _write_csv(
        csv_dir / "题材与调性推理.csv",
        [
            "编号", "适用技能", "分类", "层级", "关键词", "意图与同义词", "适用题材",
            "大模型指令", "核心摘要", "详细展开", "题材/流派", "题材别名", "核心调性",
            "节奏策略", "强制禁忌/毒点", "推荐基础检索表", "推荐动态检索表", "默认查询词",
        ],
        [
            {
                "编号": "GR-001",
                "适用技能": "write",
                "分类": "题材路由",
                "层级": "知识补充",
                "关键词": "玄幻退婚流",
                "意图与同义词": "退婚流",
                "适用题材": "玄幻",
                "大模型指令": "先压后爆",
                "核心摘要": "退婚起手",
                "详细展开": "",
                "题材/流派": "玄幻退婚流",
                "题材别名": "退婚流",
                "核心调性": "先压后爆",
                "节奏策略": "三章内反打",
                "强制禁忌/毒点": "打脸不能软收尾",
                "推荐基础检索表": "命名规则",
                "推荐动态检索表": "桥段套路",
                "默认查询词": "退婚|打脸",
            }
        ],
    )
    _write_csv(csv_dir / "命名规则.csv", ["编号", "适用技能", "分类", "层级", "关键词", "适用题材", "核心摘要"], [])
    _write_csv(csv_dir / "桥段套路.csv", ["编号", "适用技能", "分类", "层级", "关键词", "适用题材", "核心摘要", "忌讳写法"], [])

    from story_system import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "story_system",
            "玄幻退婚流",
            "--project-root",
            str(project_root),
            "--chapter",
            "1",
            "--persist",
            "--csv-dir",
            str(csv_dir),
            "--format",
            "both",
        ],
    )
    main()

    story_root = project_root / ".story-system"
    assert (story_root / "MASTER_SETTING.json").is_file()
    assert (story_root / "MASTER_SETTING.md").is_file()
    assert (story_root / "anti_patterns.json").is_file()
    assert (story_root / "chapters" / "chapter_001.json").is_file()
    assert (story_root / "chapters" / "chapter_001.md").is_file()

    payload = json.loads((story_root / "MASTER_SETTING.json").read_text(encoding="utf-8"))
    assert payload["route"]["primary_genre"] == "玄幻"
    assert payload["route"]["route_source"] == "inferred_genre_neutral"
    assert payload["route"]["recommended_base_tables"] == []
    assert payload["route"]["recommended_dynamic_tables"] == []
    assert payload["base_context"] == []


def test_story_system_persist_preserves_complete_outline_directive(
    tmp_path, monkeypatch, capsys
):
    project_root = tmp_path / "book"
    (project_root / ".canon-ledger").mkdir(parents=True)
    (project_root / ".canon-ledger" / "state.json").write_text(
        json.dumps(
            {
                "progress": {
                    "current_chapter": 2,
                    "volumes_planned": [
                        {"volume": 1, "chapters_range": "1-50"}
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    outline_dir = project_root / "大纲"
    outline_dir.mkdir()
    (outline_dir / "总纲.md").write_text(
        "\n".join(
            [
                "## 卷划分",
                "",
                "| 卷号 | 卷名 | 章节范围 | 卷内目标 | 预期结束状态 |",
                "|------|------|----------|----------|----------|",
                "| 1 | 立足 | 1-50 | 卷一站稳脚跟 | 主角在宗门站稳脚跟 |",
            ]
        ),
        encoding="utf-8",
    )
    (outline_dir / "第3章-红铜账簿.md").write_text(
        "\n".join(
            [
                "### 第三章：红铜账簿",
                "- 目标：让林川在子时前拿到账簿",
                "- 阻力：账房已经封门",
                "- 代价：暴露林川会辨认封蜡",
                "- 时间锚点：大历三年九月十七日亥时",
                "- 章内时间跨度：两个时辰",
                "- 与上章间隔：紧接上章",
                "- 倒计时状态：距秘密处决一个时辰",
                "- 本章变化：林川确认账簿封蜡被替换",
                "- 核心冲突：保住同伴与查清真相不可兼得",
                "- 视角：林川限知",
                "- Strand：账簿调查",
                "- 反派层级：小反派",
                "- 关键实体：林川、红铜账簿、王家库房",
                "- CBN：林川在亥时收到假账簿",
                "- CPNs：核对封蜡；追查送信人",
                "- CEN：林川确认内鬼来自账房",
                "- 必须覆盖节点：识别封蜡缺口；记下账房暗号",
                "- 本章禁区：不要提前揭露掌柜身份",
                "- 章末未闭合问题：真正的账簿藏在哪里？",
                "- 钩子：账簿夹层露出第二枚官印",
                "- 钩子类型：信息钩",
                "- 钩子强度：中",
            ]
        ),
        encoding="utf-8",
    )
    settings_dir = project_root / "设定集"
    settings_dir.mkdir()
    worldview_text = (
        "# 世界观\n\n"
        "## 核心规则\n"
        "- 硬约束：潮汐退去前，雾港城门不得开启。\n"
        "- 修炼：用三年时间炼成金丹。\n"
        "- 视角：主角以限知视角经历宗门大比。\n"
        "- 本书题材是仙侠修真。\n"
        "- 潮汐：北境战争节奏由月相决定。\n"
        "- 禁地：常年笼罩死寂氛围。\n"
        "- 血契：契约反转会反噬立约者。\n\n"
        "## 反馈节奏\n"
        "- 关键反馈节点：每章必须展示一次潮汐变化。\n\n"
        "## 镜像对抗\n"
        "- 反派道路：每章结尾安排一次反转。\n"
        "- 核心卖点：用冷峻短句制造悬念。\n"
    )
    (settings_dir / "世界观.md").write_text(worldview_text, encoding="utf-8")
    (settings_dir / "文风提示词.md").write_text(
        "# 文风提示词\n\n冷峻短句，减少修饰。\n",
        encoding="utf-8",
    )
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()

    from story_system import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "story_system",
            "让林川在子时前拿到账簿",
            "--genre",
            "悬疑",
            "--project-root",
            str(project_root),
            "--chapter",
            "3",
            "--persist",
            "--emit-runtime-contracts",
            "--csv-dir",
            str(csv_dir),
            "--format",
            "json",
        ],
    )
    main()

    stdout_payload = json.loads(capsys.readouterr().out)
    expected = stdout_payload["chapter_brief"]["chapter_directive"]
    chapter_path = (
        project_root / ".story-system" / "chapters" / "chapter_003.json"
    )
    persisted = json.loads(chapter_path.read_text(encoding="utf-8"))
    master = json.loads(
        (project_root / ".story-system" / "MASTER_SETTING.json").read_text(
            encoding="utf-8"
        )
    )
    review = json.loads(
        (
            project_root
            / ".story-system"
            / "reviews"
            / "chapter_003.review.json"
        ).read_text(encoding="utf-8")
    )
    markdown = chapter_path.with_suffix(".md").read_text(encoding="utf-8")

    assert persisted["chapter_directive"] == expected
    assert persisted["chapter_directive"]["goal"] == "让林川在子时前拿到账簿"
    assert persisted["chapter_directive"]["previous_chapter_gap"] == "紧接上章"
    assert (
        persisted["chapter_directive"]["chapter_change"]
        == "林川确认账簿封蜡被替换"
    )
    assert (
        persisted["chapter_directive"]["core_conflict"]
        == "保住同伴与查清真相不可兼得"
    )
    assert persisted["chapter_directive"]["viewpoint"] == "林川限知"
    assert persisted["chapter_directive"]["cpns"] == ["核对封蜡", "追查送信人"]
    assert persisted["chapter_directive"]["must_cover_nodes"] == [
        "识别封蜡缺口",
        "记下账房暗号",
    ]
    assert persisted["chapter_directive"]["forbidden_zones"] == [
        "不要提前揭露掌柜身份"
    ]
    assert (
        persisted["chapter_directive"]["chapter_end_open_question"]
        == "真正的账簿藏在哪里？"
    )
    assert (
        persisted["chapter_directive"]["hook"]
        == "账簿夹层露出第二枚官印"
    )
    assert persisted["override_allowed"]["chapter_focus"] == expected["goal"]
    assert review["must_check"] == ["识别封蜡缺口", "记下账房暗号"]
    assert review["blocking_rules"] == ["不要提前揭露掌柜身份"]
    assert "章节焦点：让林川在子时前拿到账簿" in markdown
    assert master["setting_canon"]["sources"] == [
        {
            "path": "设定集/世界观.md",
            "sha256": hashlib.sha256(worldview_text.encode("utf-8")).hexdigest(),
            "bytes": len(worldview_text.encode("utf-8")),
        }
    ]
    setting_values = [
        item["value"] for item in master["setting_canon"]["facts"]
    ]
    assert "潮汐退去前，雾港城门不得开启。" in setting_values
    assert "用三年时间炼成金丹。" in setting_values
    assert "主角以限知视角经历宗门大比。" in setting_values
    assert "本书题材是仙侠修真。" in setting_values
    assert "北境战争节奏由月相决定。" in setting_values
    assert "常年笼罩死寂氛围。" in setting_values
    assert "契约反转会反噬立约者。" in setting_values
    assert "每章必须展示一次潮汐变化。" not in setting_values
    assert "每章结尾安排一次反转。" not in setting_values
    assert "用冷峻短句制造悬念。" not in setting_values
    assert "冷峻短句，减少修饰。" not in setting_values
    volume = json.loads(
        (project_root / ".story-system" / "volumes" / "volume_001.json").read_text(
            encoding="utf-8"
        )
    )
    assert volume["volume_goal"]["summary"] == "卷一站稳脚跟"
    assert volume["volume_goal"]["name"] == "立足"
    assert volume["volume_goal"]["end_state"] == "主角在宗门站稳脚跟"


def test_markdown_writer_preserves_manual_notes_outside_markers(tmp_path):
    from data_modules.story_contracts import write_marked_markdown

    target = tmp_path / "MASTER_SETTING.md"
    target.write_text(
        "# 手工说明\n手工备注\n<!-- STORY-SYSTEM:BEGIN -->\n旧内容\n<!-- STORY-SYSTEM:END -->\n",
        encoding="utf-8",
    )

    write_marked_markdown(target, "## Auto\n新内容\n")

    text = target.read_text(encoding="utf-8")
    assert "# 手工说明" in text
    assert "手工备注" in text
    assert "## Auto" in text
    assert "旧内容" not in text


def test_story_system_default_csv_dir_routes_real_genre_seed(tmp_path, monkeypatch, capsys):
    project_root = tmp_path / "book"
    (project_root / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (project_root / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")

    from story_system import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "story_system",
            "玄幻退婚流",
            "--project-root",
            str(project_root),
            "--format",
            "json",
        ],
    )
    main()

    payload = json.loads(capsys.readouterr().out)
    route = payload["master_setting"]["route"]
    assert route["primary_genre"] == "玄幻"
    assert route["canonical_genre"] == "玄幻"
    assert route["route_source"] == "inferred_genre_neutral"
    assert route["recommended_base_tables"] == []
    assert route["recommended_dynamic_tables"] == []


def test_story_system_warns_on_placeholder_query(tmp_path, monkeypatch, capsys):
    project_root = tmp_path / "book"
    (project_root / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (project_root / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    _write_csv(csv_dir / "题材与调性推理.csv", ["编号", "关键词"], [])

    from story_system import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "story_system",
            "{章纲目标}",
            "--project-root",
            str(project_root),
            "--chapter",
            "1",
            "--csv-dir",
            str(csv_dir),
            "--format",
            "json",
        ],
    )
    main()
    captured = capsys.readouterr()
    assert "placeholder" in captured.err
    payload = json.loads(captured.out)
    assert payload["master_setting"]["route"]["route_source"] == "unclassified"
    assert payload["master_setting"]["route"]["primary_genre"] == ""
    assert payload["chapter_brief"]["override_allowed"]["chapter_focus"] == ""


def test_story_system_persist_unroutable_exits_without_contracts(tmp_path, monkeypatch, capsys):
    project_root = tmp_path / "book"
    (project_root / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (project_root / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")

    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    _write_csv(
        csv_dir / "题材与调性推理.csv",
        [
            "编号", "适用技能", "分类", "层级", "关键词", "意图与同义词", "适用题材",
            "大模型指令", "核心摘要", "详细展开", "题材/流派", "canonical_genre", "题材别名", "核心调性",
            "节奏策略", "毒点", "推荐基础检索表", "推荐动态检索表", "默认查询词",
        ],
        [
            {
                "编号": "GR-001",
                "适用技能": "story-system",
                "分类": "题材路由",
                "层级": "知识补充",
                "关键词": "玄幻退婚流",
                "意图与同义词": "退婚流",
                "适用题材": "玄幻",
                "大模型指令": "",
                "核心摘要": "",
                "详细展开": "",
                "题材/流派": "玄幻退婚流",
                "canonical_genre": "玄幻",
                "题材别名": "退婚流",
                "核心调性": "",
                "节奏策略": "",
                "毒点": "",
                "推荐基础检索表": "命名规则",
                "推荐动态检索表": "桥段套路",
                "默认查询词": "",
            }
        ],
    )

    from story_system import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "story_system",
            "rules-mystery",
            "--genre",
            "rules-mystery",
            "--project-root",
            str(project_root),
            "--persist",
            "--csv-dir",
            str(csv_dir),
            "--format",
            "json",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "rules-mystery" in captured.err
    assert "规则怪谈" in captured.err
    assert not (project_root / ".story-system").exists()
