# calendar-subscribe

公开的 iCalendar 订阅出口。仓库只保存允许公开的日历 feed、合并脚本和 GitHub Pages 工作流，不保存 Obsidian Vault 正文。

## 订阅地址

- 全部日历：`https://bryanli93.github.io/calendar-subscribe/all.ics`
- 项目时间线：`https://bryanli93.github.io/calendar-subscribe/projects.ics`
- 查看发布状态：`https://bryanli93.github.io/calendar-subscribe/`

## 工作方式

1. 独立 feed 放入 `feeds/<name>.ics`，文件名只使用小写字母、数字和连字符。
2. 推送到 `main` 后，GitHub Actions 自动运行测试和格式校验。
3. 所有 feed 会原样保留为独立订阅，同时合并为 `all.ics`。
4. 如果不同 feed 出现相同 UID，发布会失败，避免 Apple Calendar 静默覆盖事件。
5. GitHub Pages 只上传生成后的 `public/`，不上传脚本、测试或私有来源。

## 新增日历

新增 `feeds/habits.ics`、`feeds/travel.ics` 等文件即可。每个源文件必须是 UTF-8 编码、RFC 5545 结构完整的 `VCALENDAR`，并且每个 `VEVENT` 都包含唯一的 `UID` 和 `DTSTART`。

> [!WARNING]
> `feeds/` 与 Pages 中的内容都是公开信息。只有已经去敏、明确允许公开的事件才能进入此仓库。

