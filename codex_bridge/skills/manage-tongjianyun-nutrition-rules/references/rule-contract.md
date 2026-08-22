# 营养规则工具契约

## 支持的营养指标

`energy`、`protein`、`fat`、`carbohydrate`、`calcium`、`vitamin_a`、`vitamin_b1`、`vitamin_b2`、`vitamin_c`、`vitamin_e`、`niacin`、`potassium`、`magnesium`、`iron`、`zinc`、`phosphorus`、`selenium`、`carotene`、`fiber`、`cholesterol`。

## changes 对象

```json
{
  "version": "2.0",
  "source": "规则依据",
  "change_reason": "业务原因",
  "rules": {
    "vitamin_c": {
      "formula": "per_100g * grams / 100 / day_count * edible_ratio * retention_rate",
      "edible_ratio": 1,
      "retention_rate": 0.7,
      "lower_percent": 80,
      "upper_percent": 0,
      "enabled": true
    }
  },
  "macro_factors": {"carbohydrate": 4, "fat": 9, "protein": 4},
  "macro_ranges": {"carbohydrate": [50, 65], "fat": [20, 30], "protein": [10, 20]},
  "animal_protein_target": 30,
  "animal_soy_protein_target": 50
}
```

只提交需要改变的字段。`upper_percent` 填 `0` 表示不设上限。

## 公式语法

允许变量：

- `per_100g`：食物成分表中每 100 克的营养值
- `grams`：当前食材重量（克）
- `day_count`：食谱天数
- `edible_ratio`：可食部比例，范围 0–1
- `retention_rate`：烹调保留率，范围 0–1

允许运算符：`+`、`-`、`*`、`/`、一元正负号。

允许函数：`min`、`max`、`abs`、`round`。不允许命名参数；`round` 小数位范围为 -12 到 12。

默认公式：

```text
per_100g * grams / 100 / day_count * edible_ratio * retention_rate
```

禁止任何其他变量、函数、属性、索引、条件表达式、集合、导入或代码执行。

## 状态与确认

状态顺序为：`草稿` → `待审核` → `已发布`。发布会停用先前版本。

- 发布确认词：`确认发布`
- 回滚确认词：`确认回滚`

确认词只能在用户已经明确确认具体规则版本后传给工具，不能由 Agent 自行补全。
