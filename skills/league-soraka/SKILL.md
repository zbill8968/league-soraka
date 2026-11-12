# lol-augment

ARAM: Mayhem（海克斯大乱斗）增幅推荐助手。游戏内的实时识别由独立 Electron 浮层完成，本插件负责监督浮层、维护 OP.GG 数据快照，并给模型提供可调用的工具。

## 什么时候用

- 用户问「这局海克斯选哪个」「增幅怎么重置」「F8 不弹东西」。
- 用户问「这局队友什么水平/什么风格」「人头狗/城墙/队友画像」。
- 用户要刷新/核对 OP.GG 数据版本。

## 工具

| 工具 | 用途 |
| --- | --- |
| `lol_augment_status` | 浮层是否在跑、数据 patch、英雄覆盖数、热键、上次退出码 |
| `lol_augment_refresh_data` | 重新抓取 OP.GG 增幅统计（可带 `limit` 做冒烟测试） |
| `lol_augment_explain_policy` | 用三张卡的分数跑一遍「保一 / 重置二 / 追更高档」策略并解释 |

## 队友画像

选人阶段点胶囊，面板会出现「队友画像」：5 名队友各 2–3 个标签（城墙、人头狗、隐身哥、移动泉水…）+ 证据 + `x/n 场`。进入 ARAM 选人后浮层会后台预取（不自动弹窗），点胶囊直接渲染；规则/阈值见 `docs/teammate-tags.md`，实现见 `overlay-py/tagrules.py` + `overlay-py/teammates.py`。预取开关在 `data/config.json` 的 `prefetch_champ_select` / `prefetch_champ_select_all_modes`，临时关闭用 `--no-prefetch-champ`。

命令行：

```bash
python scripts/teammates-cli.py            # 当前选人 5 人
python scripts/teammates-cli.py --demo     # 无客户端样例
python scripts/teammates-cli.py --json
python scripts/teammates-cli.py --stats    # 指标，校准阈值
python scripts/teammates-cli.py --probe    # 数据源诊断
python scripts/teammates-cli.py --clear-cache
```

数据源：优先 LCU 列表 + LCU 单局详情（10 人）；失败时用 SGP SUMMARY/DETAILS。SGP 不是隐私绕过；查不到时按 `anonymous/private/unavailable/no_aram/low_sample` 显示原因，不要硬编标签。

## 关键事实

- **不要**在热路径上调模型：三张卡出现后用户按 F8，浮层在 1 秒内自己给出结论。模型只在数据缺失、新英雄、复盘时介入。
- 数据来自 OP.GG 的 `aram-mayhem` 页面（无公开 API，解析页面内嵌的 RSC 数据），落在 `data/catalog.json` 与 `data/stats.json`。中文名/说明由 `fetch-zh-names.mjs` 补 `nameZh` / `descZh`。
- 三张增幅卡下方有大号评级徽章；浮层启动后每 300ms 自动监听卡面，游戏内 reroll 后自动重算，无需再点浮层刷新。
- 名称与 OP.GG 翻译不一致时，`classify_local` 会用卡下说明匹配 `descZh` 兜底；仍找不到才显示 `?`。
- 识别主路径是图标匹配（`data/icon-index.json`），**部分增幅共用同一张美术图**，这类卡会被标为「?」，需要 OCR 兜底（下一步）。
- 本地阈值可用根目录 `标签管理.cmd` 调（本地 127.0.0.1 页面，写入 `data/tag-overrides.json`）；保存后下一次分析生效。
- 已核实 Riot 自 2026-10-06 起默认阻止未知第三方读取游戏内存，所以只做截屏识别；**不要**建议注入、读内存或自动点击。

## 排障

1. 按 F8 没反应：确认浮层进程在跑（`lol_augment_status`），热键是否被占用。
2. 一直提示标定：按 F4，依次点三张卡的图标中心，保存。
3. 数据过期：`lol_augment_refresh_data`。
4. 识别不出来：先按 F9 存一张截图到 `data/screenshots/`，再检查 `data/layout.json` 的坐标是否偏。
