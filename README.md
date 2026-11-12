# League Soraka — 海克斯大乱斗助手

英雄联盟「海克斯大乱斗」（ARAM: Mayhem）的本地助手：

- **对局中**：点右上角索拉卡头像 → 截屏识别三张增幅卡 → 按当前英雄的 OP.GG 数据给 S/A/B/C/D 评级 → 在三张卡下方显示大号评级徽章（S/A/B/C/D，90+ 红 / 80+ 橙 / 70+ 黄 / 60+ 浅灰 / 更低深灰）。左侧窗口不再重复推荐海克斯，改为显示队友标签、双方开黑和 OP.GG 出装。海克斯选择窗口由玩家手动打开；窗口出现后浮层每 300ms 检查卡面，reroll 后约 1 秒自动更新卡下徽章。左侧信息窗口不会自动弹出，需要手动点索拉卡。
- **选人阶段**：点索拉卡头像 → 列出候选英雄按 OP.GG 强度排序，实时同步上方备选池和已选英雄；每 300ms 自动刷新，不自动弹窗。选人界面不再查询队友战绩。

**不调用大模型、不消耗 token**。增幅推荐只读本地客户端；队友画像会在本机用客户端的 `league-session` 令牌请求官方 SGP 战绩接口（不上传、不写回游戏）。

> **关于作者**：我是一个完全不懂编程的非技术人员，这个项目是在 **DeepSeek V4.1 Flash** 的帮助下一点点做出来的。代码、设计和文档都还有很多不成熟的地方，欢迎各位大佬提 Issue、PR 或改进建议，尤其欢迎性能、OCR、UI 和英雄数据方向的指正。
>
> **About**：This project was built by a non-programmer with the help of **DeepSeek V4.1 Flash**. Feedback, issues and pull requests are very welcome.

## 交互（纯鼠标，无任何快捷键）

| 操作 | 效果 |
| --- | --- |
| 点右上角索拉卡头像 | 分析当前画面（对局=增幅推荐，选人=英雄评级），头像会眨一下眼 |
| 点头像右上角「×」 | 退出程序 |
| 面板按钮 | 海克斯界面只有右上角「×」收起；选人界面不再有按钮 |
| 面板上右键 | 收起回头像（不会退出） |

## 启动方式

| 文件 | 用途 |
| --- | --- |
| `启动面板.vbs` | 无窗口启动（供登录自启调用，平时不用双击） |
| `启动面板-管理员.cmd` | 选人阶段优先用这个；新版 `lcu.py` 会先读客户端日志（普通权限通常也行），失败时再提权 |
| `关闭面板.cmd` | 彻底退出面板进程 |
| `测试截图.cmd` | 把任意截图拖上去，离线跑一遍识别 |
| `refresh-data.cmd` | 重新抓 OP.GG 增幅数据（数据本身随仓库附带） |
| `标签管理.cmd` | 打开本地标签调参后台（改阈值/命中率/启用状态） |

## 打包给朋友（无需装 Python/Node）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-portable.ps1
```

产物：`dist\LeagueSoraka\`（内置 Python + app + data + 启动器）和
`dist\LeagueSoraka-0.5.4-portable.zip`（约 29 MB）。这个版本不需要装 Python/Node，也不依赖
PyInstaller；内置运行时只保留 Pillow。

压缩包解开后双击 `启动LeagueSoraka.vbs` 即可（会请求一次管理员权限），细节见包内的
`使用说明.txt`。打包版内置分类器（`overlay-py/scoring.py`、`overlay-py/classify_local.py`），
不依赖 Node；数据更新需要回到源码目录重新打包。

## 开黑（组队）提示

点胶囊时会顺带判断本局有没有开黑小队：

- **选人界面**：面板底部会列出「我方 2 人开黑：提莫 + 阿狸」这样的分组；查不到组队数据时整块隐藏，不显示占位文字。
- **对局中**：标题下多一行「开黑：我方 提莫+阿狸；敌方 盖伦+金克丝、拉克丝+薇恩」，直接列出谁和谁一队；没有开黑则写「开黑：无（每人各自单排）」。

数据来源是客户端自己维护的分组：`GET /lol-gameflow/v1/session` 里 `gameData.teamOne/teamTwo` 的每个玩家都有
`teamParticipantId`，同一队的玩家共享这个 id（Akari 用的也是它）。

客户端在回到大厅后**仍保留上一局的 gameData**，所以读到的可能是上一局的开黑信息。为此有三道校验：

1. `phase` 必须是 `ChampSelect / GameStart / InProgress / Reconnect`，大厅里一律不采用；
2. 数据里的玩家必须与当前选人会话的 5 个 puuid 对上（对局中则与实时客户端 10 个英雄名换算出的 id 对上）；
3. 选人阶段客户端常常还没填好 gameData，面板会先秒出英雄列表，再在后台每 2 秒重试最多 30 秒补上开黑信息；
   始终拿不到就写「暂不可用」，不会误报「没人开黑」。

阵营标签按「你在哪一队」显示 我方 / 敌方。实测对局中示例：`敌方 3 人开黑`、`我方 2 人开黑`。

> 说明：LCU 的战绩**列表**对「其他玩家」只返回他自己那一条 participant 记录；但单局
> `/lol-match-history/v1/games/{gameId}` 实测返回 10 人。队友画像当前首选 SGP SUMMARY
> （每项 `json` 自带完整 10 人），失败才退回 LCU 列表 + 单局详情。

## 增幅评分徽章

对局中识别三张增幅卡后，会在**每张卡（游戏内刷新按钮）下方**弹出大号 OP.GG 评级徽章：**大号 S/A/B/C/D**，下面一行小分数，按 90+ 红 / 80+ 橙 / 70+ 黄 / 60+ 浅灰 / 更低深灰上色；字号和尺寸会随分辨率/卡片大小缩放。左侧窗口在整局游戏内都可以打开：显示队友标签、双方开黑情况和 OP.GG 高胜率出装（3 套推荐出装，每套 5 件装备大图标，标 1st–3rd 并给出参考胜率，首次需联网、缓存 6 小时），不再重复海克斯推荐；右上角「×」只收起窗口。不在对局时点按钮会提示「未检测到对局」。

- 只在玩家死亡/回泉水后的短窗口内检查卡面；不会因为达到 3/7/11/15 级就自动弹窗。海克斯窗口由玩家手动打开，打开后 reroll 会自动重新截图、识别、评分并更新卡下徽章；左侧信息窗口始终手动打开。等级只用于成长型海克斯的分数系数（按实际选卡时的等级）。
- 名称与 OP.GG 中文翻译不一致时，会 OCR 卡片下方的说明，用 `catalog.descZh` 做最长公共子串兜底；找不到再用英雄表，最后用全英雄平均分。
- 徽章位置由 `data/layout.json` 的 `descBand` / `scoreBand` 控制；换分辨率或 HUD 缩放时调这两个值。
- 布局示意：[全屏](docs/layout-preview.png) · [局部放大](docs/layout-preview-zoom.png)。
- 自动扫描不会先收起徽章；选完海克斯/复活、离开卡面或连续识别不到卡时清理，通常 1 秒内消失；左侧信息面板改为手动点开，不会自动弹出。

## 成长型海克斯等级加权

部分海克斯是永久/任务成长型，越早选收益越高。当前清单和系数：

- 3–6 级：×1.08
- 7–10 级：×1.03
- 11–14 级：×0.97
- 15 级以上：×0.92
- **超凡邪恶**特殊：3–10 级 ×1.03，11–14 ×0.97，15+ ×0.92

参与加权的首批：钢化你心、高压锅、吞噬灵魂、命运之武器、超凡邪恶、叠角龙、牙仙子、伤不过三、无限循环往复、吃过路兵、濒死悟道、科学狂人、物法皆修、魄罗爆破手、升级：收集者、弹跳蜜蜂、热舞怪兽、可靠武器、泰坦的坚决、保持坚定。数据在 `data/growth-augments.json`。

王冠之重、坦克引擎、缩小引擎、扇巴掌这类阵亡会损失层数的海克斯**不参与**等级加权，避免死一次后还把早期系数算高。

## 队友标签（对局内）

选人界面不再显示队友画像，避免 SGP 查询拖慢选人面板。进入对局后，左侧信息面板会显示 4 名队友的标签和最近 20 场海克斯均分；例如：

```
[提莫]  城墙 · 心之钢信徒 · 海克斯均分 86.4        承伤占比 43% · 心之钢 100% · 死亡/分 0.25 · 12/12 场
[阿狸]  人头狗 · 刮痧师傅        击杀占比 41% · 伤害占比 14% · 伤害/击杀 1273 · 10/10 场
[盖伦]  隐身哥 · 样本不足        伤害 9% · 承伤 11% · 参团 29% · 10/10 场
```

- **采样**：SGP 一次拉最近 20 场（每场的 `json` 自带完整 10 人），筛 `mapId=12`、`queueId 2400/450`、≥5 分钟。
- **贴标签门槛**：正面/中性行为 n≥5 且 ≥40% 场次命中；负面/调侃 n≥8 且 ≥50%；稳定类/节奏类 5–7 场可低置信；n<5 只出数据质量。
- **新增标签**：`K头者`（射手/战士/刺客 ≥60% 场次，且这些场次出「收集者」≥80%）、`数学奇才`（前三个海克斯含暴击率强化，最终暴击 ≥125%）。已移除 `六神装`、`裸装哥`、`收割机`。
- **查不到会说明原因**：`神秘人·匿名者` / `神秘人·隐藏生涯` / `数据暂不可用` / `非大乱斗玩家` / `样本不足`，不把所有情况混成一个「神秘人」。
- **数据源**：优先本机 LCU 列表 + 单局详情（实测约 0.1 秒/人），LCU 不可用时回退 SGP SUMMARY。SGP 不是隐私绕过。

队友行会显示最近 20 场已选海克斯的 OP.GG 平均分（`avgAugmentPerf`）。完整标签表、指标公式、状态机、阈值见 [docs/teammate-tags.md](docs/teammate-tags.md)。

命令行调试：

```bash
python scripts/teammates-cli.py            # 当前选人 5 人
python scripts/teammates-cli.py --demo     # 无客户端样例
python scripts/teammates-cli.py --json
python scripts/teammates-cli.py --stats    # 打印指标，校准阈值
python scripts/teammates-cli.py --probe    # 检查数据源
python scripts/teammates-cli.py --clear-cache
```

### 选人预取

- 进入 ARAM 选人后每 1.5 秒探测一次 `gameflow-phase`；确认 `ChampSelect`、5 人齐、英雄齐后，在后台跑一遍 `_champ_select_worker`。
- 预取只存状态，不渲染、不弹窗；面板仍由用户点胶囊打开。
- 点胶囊时如果预取完成，`_show_prefetched()` 同步渲染，**0.3 秒内**出面板；如果预取还在跑，先显示“选人分析中…”，结果到了自动出。
- 离开选人清空预取；同一局只预取一次；右键收起后预取仍在，再点秒开。
- 配置 `data/config.json`：
  - `prefetch_champ_select`（默认 true）：总开关；
  - `prefetch_champ_select_all_modes`（默认 false）：除 ARAM 外，SR 选人也预取；
- 临时关闭：`--no-prefetch-champ`。
- 海克斯自动刷新由 `data/config.json` 的 `auto_refresh_augments` 控制（默认 true）；临时关闭用 `--no-auto-augment`。

## 客户端数据怎么读（已不需要管理员）

腾讯客户端不写 lockfile，但它的启动参数（`--app-port` / `--remoting-auth-token` / `--rso_platform_id`）
会被原样打印在 `LeagueClient\*_LeagueClientUx.log` 的第一行。`overlay-py/lcu.py` 按顺序尝试：

1. WMI 读 `LeagueClientUx.exe` 命令行（需要管理员，能拿到也不拒绝）；
2. 读客户端日志里的同一行（**不需要管理员**，安装目录首次发现后缓存在 `%LOCALAPPDATA%\LeagueSoraka`）。

拿到凭据后：

- 选人阶段：`/lol-champ-select/v1/session` 取候选和我方阵容；
- 对局中：`/lol-gameflow/v1/session` 的 `gameData.teamOne/teamTwo` 里每个玩家带 `teamParticipantId`，同队共享同一 id → 精确开黑分组；
- 拿不到分组时：`overlay-py/sgp.py` 用客户端的 `league-session` 令牌调 SGP
  （`/match-history-query/v1/products/lol/player/{puuid}/SUMMARY`），按「同队共现 ≥2 场」推断，标「疑似」。

## 目录

```
overlay-py/          浮层（Python/Tk）\n  avatar.py          索拉卡头像与眨眼帧生成
  overlay.py         主程序：截屏、OCR、渲染、鼠标交互
  champselect.py     英雄名匹配与分区
  lcu.py             读客户端本地接口（日志/WMI 凭据 + 选人 + 战绩）
  sgp.py             客户端 league-session 令牌 + SGP 战绩
  tagrules.py        队友画像：指标聚合、规则、命中率、互斥/排序（纯函数）
  teammates.py       队友画像：LCU/SGP 拉取、归一化、缓存、并发、demo
  premade.py         开黑分组
  ocr_worker.py      常驻 PowerShell OCR 进程
  classify_worker.py 常驻 Node 分类进程
lib/                 共享逻辑（ESM）
  hash.js            图标描述子（覆盖度 + 颜色）
  matcher.js         图标匹配
  score.js           OP.GG 分数 → 百分位 → S/A/B/C/D
  policy.js          保/重置/追档策略（纯函数）
  index.js           DSH 宿主插件（监督浮层 + 工具 + skill）
scripts/             OP.GG 抓取、图标/头像索引构建、OCR、分类器、teammates-cli
docs/                teammate-tags.md（标签规范）
data/                catalog / stats / champions / items / 图标 / 头像 / layout / screenshots
test/                Node 单测 + Python 队友画像单测
```

## 性能（实测）

| 环节 | 耗时 |
| --- | --- |
| 截屏（只截卡片区域） | 0.03 s |
| OCR（常驻 worker） | 0.03–0.20 s |
| 分类 + 策略（常驻 Node） | 0.02 s |
| **合计** | **约 0.1–0.3 s** |

## 数据更新

| 时机 | 动作 | 耗时 |
| --- | --- | --- |
| 每次启动 | 检查 OP.GG 版本号（后台） | 约 0.7 s |
| 检测到对局英雄 | 只抓这个英雄的表格并热加载 | 约 1 s |
| OP.GG 版本号变化 | 全量刷新 173 个英雄 | 约 41 s（后台） |
| 手动 `refresh-data.cmd` | 全量刷新 | 约 41 s |

版本号不变时不做全量刷新；日常只按你实际玩的英雄增量更新，多出来的没用增幅会在全量刷新时被裁掉。

## 已知限制

1. `data/layout.json` 按 1920×1080 标定，换分辨率或 HUD 缩放需要重标。
2. 仅 Windows（Windows.Media.Ocr + Tk），且只在腾讯服中文客户端验证过。
3. 增幅评级用 OP.GG 英雄专属 `performance`；选人评级用 OP.GG 英雄排名（由胜率折算，OP.GG 未公开原始胜率）。
4. 队友画像描述的是最近 20 场的行为倾向，不是人格/技术定论；隐藏生涯、匿名、跨区、SGP 超时会显示对应原因，不会硬编。
5. 增幅名称与 OP.GG 翻译不一致时，会 OCR 卡下说明并与 `catalog.descZh` 匹配；`descZh` 由 `fetch-zh-names.mjs` 更新。
6. 增幅分数徽章的位置由 `layout.json` 的 `descBand` / `scoreBand` 控制；换分辨率或 HUD 缩放时可微调这两个值。
7. 只读屏 + 只读客户端接口 + 置顶窗口，不注入、不读内存、不模拟点击；Riot 不背书，风险自负。

## 标签调参后台（本地）

双击根目录的 `标签管理.cmd`，会自动打开本地页面 `http://127.0.0.1:8765/`：

- 左侧搜索/选择标签，右侧用下拉框和数字框改阈值、命中率、最少场次、启用状态、标签名和语气；
- 保存写到 `data/tag-overrides.json`，下一次队友分析使用新规则；
- 「恢复默认」清空覆盖；
- 只绑定本机 127.0.0.1，不需要登录/联网；已经预取好的选人快照需要重新进一次选人。

「大爹」默认已收紧为伤害占比 ≥30%、胜率 ≥50%、逐局 ≥28% 且获胜、最少 8 场、命中率 50%；仍可在后台继续调整。

## 开发

```sh
node scripts/fetch-opgg.mjs                # 抓增幅数据
node scripts/fetch-champions.mjs           # 抓英雄强度
node scripts/fetch-champions.mjs --tags-only  # 只合并 DDragon 职业标签（Tank/Mage/...）
node scripts/fetch-zh-names.mjs --champions 20  # 补 nameZh / descZh（OP.GG 中文说明）
node scripts/fetch-items.mjs               # 补暴击装备表（数学奇才用）
python scripts/build-champ-index.py        # 构建英雄头像索引
python overlay-py/avatar.py                # 重新生成眨眼帧
node --test --test-isolation=none test/policy.test.mjs test/score.test.mjs test/matcher.test.mjs
python -m unittest discover -s test -p "test_*.py" -v   # 队友画像单测
```
## 反馈与许可

- 遇到问题、想提建议：直接开 GitHub Issue，附上复现步骤和日志（请先删除个人信息）。
- 想改代码：欢迎 Fork / PR；不熟悉代码也欢迎提需求。
- 许可证：MIT，见 [LICENSE](LICENSE)。
- 第三方素材与数据说明：见 [NOTICE.md](NOTICE.md)。
- 本项目不是 Riot Games 或 OP.GG 官方项目。

