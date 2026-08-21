# 初始化收集与权威分流（内部模型）

> 本文件帮助 `/canon-ledger-init` 组装统一 `canon_ledger.py init`
> 参数并解释它们的权威去向，不是另一个执行脚本或“字段填满”门禁。
> 必需输入只有 exact target、书名和题材；其它字段只在作者明确给出时传递。

```json
{
  "required": {
    "project_dir": "",
    "title": "",
    "genre": ""
  },
  "genesis_import": {
    "protagonist": {
      "name": ""
    },
    "world": {
      "scale": "",
      "factions": "",
      "power_system_type": "",
      "social_class": "",
      "resource_distribution": "",
      "currency_system": "",
      "currency_exchange": "",
      "sect_hierarchy": "",
      "cultivation_chain": "",
      "cultivation_subtiers": ""
    },
    "golden_finger": {
      "name": "",
      "type": "",
      "visibility": "",
      "irreversible_cost": ""
    },
    "characters": {
      "heroine_names": [],
      "co_protagonists": []
    }
  },
  "planning_or_metadata": {
    "target_words": null,
    "target_chapters": null,
    "core_selling_points": [],
    "target_reader": "",
    "platform": "",
    "one_liner": "",
    "core_conflict": "",
    "first_volume_goal": "",
    "protagonist_desire": "",
    "protagonist_flaw": "",
    "protagonist_archetype": "",
    "protagonist_structure": "",
    "heroine_config": "",
    "heroine_role": "",
    "co_protagonist_roles": [],
    "antagonist_tiers": {},
    "antagonist_level": ""
  },
  "style_only": {
    "golden_finger_style": "",
    "author_preferences": []
  }
}
```

## 路由表

| 收集组 | init 工具映射 | 权威去向 |
|---|---|---|
| `required.project_dir/title/genre` | 三个必需位置参数 | target 定位；title/genre 会进入净化后的 `initial_canon` |
| `genesis_import.protagonist.name` | `--protagonist-name` | 非空作者输入进入一次性 verified genesis snapshot |
| `genesis_import.world.*` | 同名 world/currency/sect/cultivation 参数 | 非空初始硬事实进入 `author_axiom_snapshot` admission |
| `genesis_import.golden_finger.*` | name/type/visibility/irreversible-cost 参数 | 非空机制事实进入 genesis admission；文风不在此组 |
| `genesis_import.characters.*` | `--heroine-names/--co-protagonists` | 仅作者明确给出的初始人物身份进入 genesis admission；感情线定位和人物作用不进入 |
| `planning_or_metadata.*` | target/reader/platform/selling-points 和人物设计字段是可选 init 参数；其余交 plan | 配置、模板或软计划；欲望、缺陷、动机、人设、感情线与对立定位均不进入 `initial_canon`，也不因履约与否生成事实 blocker |
| `style_only.*` | `--golden-finger-style` 只用于非权威元数据/模板；通用偏好在 init 后交 `/canon-ledger-learn` | 排除在 `initial_canon`、genesis admissions、author-axiom digest、HEAD 和人工 cases 之外 |

## 组装规则

1. 不存在的可选字段直接省略 CLI flag；不用模板值、模型猜测或“无”补满。只有作者明确声明“没有”且该否定本身需要成为长期事实时，才传递对应值。
2. 列表/映射在调用 init 工具前按该 CLI 要求序列化；不把未登记字段塞入 `state.json` 后再当作 Canon。
3. 统一 init 工具将净化后的 `MASTER_SETTING.initial_canon` 经 `cutover_chapter=0` 的 verified snapshot 导入 genesis。这些事实在 `canon-v3 author-axioms` 中显示为 `genesis_admissions`，不是 managed author-axiom `records`。
4. 初始化后修改这些硬事实时，不再调 init。改用 managed author-axiom draft 和 `prepare → decide → finalize`；覆盖初始值还必须绑定 exact `genesis_overrides`。
5. 大纲、人物动机、剧情取舍、节奏和文风都不是默认强制事实审查项。
