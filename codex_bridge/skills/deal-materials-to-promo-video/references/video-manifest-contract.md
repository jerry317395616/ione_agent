# Promotional Video Manifest Contract

The manager MCP accepts business content only and renders it through an allowlisted Remotion template. Do not emit JSX, JavaScript, CSS, shell commands, remote scripts, or arbitrary URLs.

## Recommended Narrative

Use 60-90 seconds and 7-9 scenes by default:

1. `cover`: customer, project theme, shared objective.
2. `context`: verified current situation and business pressure.
3. `challenge`: two to five verified pain points.
4. `solution`: the proposed response and its boundaries.
5. `capability`: capabilities tied directly to the needs.
6. `roadmap`: discovery, delivery, validation, launch, and operation.
7. `value`: sourced outcomes or clearly labeled proposed indicators.
8. `closing`: next meeting, scope confirmation, pilot, or another concrete action.

The first scene must be `cover`; the last must be `closing`. A video must contain 6-12 scenes and run for 30-180 seconds. Each scene may last 3-30 seconds.

## Payload

```json
{
  "title": "区域医疗智能运营平台建设方案",
  "customer": "客户名称",
  "brand": "I-ONE AI",
  "template": "medical-enterprise",
  "aspect_ratio": "16:9",
  "language": "zh-CN",
  "call_to_action": "确认试点范围并召开项目启动会",
  "scenes": [
    {
      "kind": "cover",
      "title": "区域医疗智能运营平台",
      "subtitle": "面向客户决策与项目启动",
      "bullets": [],
      "narration": "围绕客户当前的运营目标，我们提出一套可分阶段落地的建设方案。",
      "duration_seconds": 6,
      "asset_file": "",
      "evidence": "CRM Deal 项目名称；Word 方案封面"
    },
    {
      "kind": "closing",
      "title": "携手推进下一步",
      "subtitle": "从范围确认与试点计划开始",
      "bullets": ["确认业务范围", "确定双方团队", "安排启动会议"],
      "narration": "建议下一步由双方共同确认范围、团队和试点计划。",
      "duration_seconds": 8,
      "asset_file": "",
      "evidence": "Word 方案：下一步建议"
    }
  ]
}
```

## Scene Fields

- `kind`: One of `cover`, `context`, `challenge`, `solution`, `capability`, `roadmap`, `value`, `closing`.
- `title`: Required, at most 100 characters.
- `subtitle`: Optional, at most 220 characters.
- `bullets`: Up to six items, at most 140 characters each.
- `narration`: Optional Chinese narration and subtitle text, at most 800 characters.
- `duration_seconds`: Between 3 and 30.
- `asset_file`: Optional exact File document name or attachment filename from the source package. Only Deal-owned PNG, JPEG, or WebP images are accepted.
- `evidence`: Required in practice for factual scenes. Name the source, without copying private URLs.

## Template And Output

- Use `medical-enterprise` for healthcare, medical insurance, hospital, screening, rehabilitation, or public-health Deals.
- Use `enterprise` for other business Deals.
- Use `16:9` unless the user explicitly requests a mobile portrait video.
- Submit `draft` for a 720p review render and `final` for a 1080p customer deliverable.
- Remotion produces the MP4 and cover. The renderer derives an SRT subtitle file from scene narration and timing.
