# i18n 批次 3：把中文为主的页面做成中英双语

给执行这项任务的 Agent。这份文档自成一体，读完就能开工；遇到文档没覆盖的情况，先看第 9 节“做不了就停下来问”。

任务 ID：`i18n-batch3`　　仓库：GitHub `yiyabo/GAgent`（Mac：`/Users/apple/code/GAgent-main`）
基线：main 上 `f5dc371e`（含 #55 框架、#56 批次 2）。**开工前先 `git fetch` 并确认 main 没有比它更新的 i18n 提交。**

---

## 1. 背景，一句话

前端要支持中文和英文。框架、语言切换、两个语言包、防回归测试都已经做好（#55、#56）。批次 1、2 已把顶栏和 5 个原本是英文的页面迁完。**批次 3 是剩下的：原本以中文为主的页面（以及一部分夹杂的英文页面）。**

默认语言按部署决定：CityU（620b）默认英文，.8 默认中文，访问者自己点选过的优先。`.8` 这次不动，也不要碰任何部署。

## 2. 你的目标与完成标准

把 `src/i18n/hardcodedBaseline.ts` 里列的 **53 个文件 / 710 处**写死的界面文字，迁到语言包。

完成的样子：
- 这些文件里的界面文字都走 `t('组.键')`，`en.json` 与 `zh-CN.json` 同步加键。
- 迁完的文件，**把它从 `hardcodedBaseline.ts` 里删除**（计数降到 0）。
- `npm run lint`、`npx tsc --noEmit`、`npx vitest run`、`npm run test:visual` 全绿。
- 在真实浏览器里，两种语言各看一遍每个迁过的页面（见第 7 节）。

**不要求**一个 PR 做完。建议分 4 个 PR（第 5 节），每个都能独立合并。

## 3. 绝对不能碰的东西

1. **不要部署**，不要动 620b / .8，不要改 `deploy/`、`.github/` 之外你不理解的东西。
2. **不要删除或改写 Agent 执行命令的能力**：`terminal_session`、`execute_code`、`/api/v1/terminal`、沙箱都在 `app/`、`tool_box/`。本任务只改 `web-ui/`，**不要改后端**。
3. **不要改 `hardcodedBaseline.ts` 里别人文件的数字来“让测试过”**。数字只许降，规则见第 6 节。
4. **不要放宽 `i18n.test.ts` 的扫描器或删断言来变绿**。如果你认为扫描器有误报，停下来问（第 9 节）。
5. 不要 force-push，不要 rebase；合并 main 用 `git merge origin/main`。
6. 不要改 `CLAUDE.md` / `AGENTS.md` / 交接文档（`docs/` 是私有、未入库）。

## 4. 开工前必须读的东西（按顺序，约 15 分钟）

1. `web-ui/src/i18n/README.md`：框架怎么用。
2. `web-ui/src/i18n/useT.ts`：`useT()`（组件里）与 `translate()`（非组件里）。
3. `web-ui/src/i18n/statusLabels.ts`、`memoryLabels.ts`：**已有的共享词**，先复用，别重复造。
4. 一个已迁移好的例子：`web-ui/src/pages/Memory.tsx` 与 `components/memory/SaveMemoryModal.tsx`。看它们怎么用 `t()`、怎么带插值。
5. `web-ui/src/i18n/i18n.test.ts` 末尾的 “hard-coded UI text only shrinks”：这就是你的验收尺子。

### 工具链速查

| 要做的事 | 怎么做 |
|---|---|
| 组件里取翻译 | `const t = useT();` 然后 `t('plans.title')` |
| 带变量 | `t('plans.tagTasks', { count: n })`，语言包写 `"Tasks {{count}}"` |
| 非 React 代码（store、工具函数） | `import { translate } from '@/i18n/useT'`，`translate('key')` |
| 键是否存在 | 编译器检查：写错键名是类型错误 |
| 键由变量拼出来 | 编译器**查不到**，必须在 `src/i18n/statusLabels.test.ts` 末尾那个 “keys assembled from a value at runtime” 里登记每个取值（见第 6 节） |
| 看某个文件还剩多少写死文字 | 见第 6 节 |

## 5. 分批建议（4 个 PR，每个独立可合并）

数字来自 `hardcodedBaseline.ts` 的当时快照。

### PR A：英文为主的页面 → 补中文（最简单，先做）
这些文件现在是英文，**要写中文**。较机械。**但先看一眼**：`job-log/ResultSection.tsx`、`job-log/LogSection.tsx`、`ArtifactGallery.tsx`、`tasks/detail/TaskExecuteModal.tsx` 里还夹着少量中文（5～24 字），这些要反过来**补英文**，不要漏。

| 文件 | 处数 |
|---|---|
| `components/layout/ArtifactsPanel.tsx` | 33 |
| `components/chat/message/ToolProgressCard.tsx` | 30 |
| `components/chat/ToolResultCard.tsx` | 21 |
| `components/layout/ExecutorPanel.tsx` | 21 |
| `components/layout/artifactPreview.tsx` | 17 |
| `components/tasks/detail/TodoListPanel.tsx` | 17 |
| `components/layout/DAGSidebar.tsx` | 16 |
| `components/tasks/detail/index.tsx` | 16 |
| `components/chat/job-log/ResultSection.tsx` | 11 |
| `components/chat/FileTreeSelector.tsx` | 9 |
| `pages/Register.tsx`、`pages/Login.tsx` | 9 + 7 |
| `components/chat/FileUploadButton.tsx` | 8 |
| `components/chat/message/ToolResultDrawer.tsx` | 7 |
| `components/common/ErrorBoundary.tsx` | 7（**类组件，见第 8 节陷阱 3**） |
| 其余小文件 | `LogSection`、`MessageActions`、`ArtifactGallery`、`PlanTreeVisualization`、`TypingIndicator`、`ResumeRunAction`、`ClassicChatLayout`、`System`、`App`、`Wordmark` 等 |

### PR B：计划工作区与任务详情（中文为主 → 补英文）
`components/layout/planWorkspace/`（`PlanWorkspace` 38、`SelectedTaskDetail` 23、`PlanRunHistory` 6、`TaskOutline` 5）、`components/layout/planArtifacts/PlanTaskArtifacts.tsx`（26）、`components/tasks/detail/`（`TaskDetailSections` 45、`TaskExecuteModal` 23）。**这批最大，最容易出错。**

### PR C：聊天与技能
`ChatSidebar`（36）、`ChatMainArea`（18）、`HeroWelcome`（13）、`ResponseStyleSelector`（12）、`SkillLearningPanel`（25）、`ThinkingProcess`（3）、`FigureFineTuneModal`（13）、`SessionArtifactImage`（8）、`RecallReferences`（3）、`UploadedFilesList`（2）、`chat/message/index.tsx`（13）、`job-log/index.tsx`（21）、`job-log/constants.tsx`（13，**模块级文字，见陷阱 2**）、`ArtifactVersionPanel`（7）、`ResearchWorkspace`（10）。

### PR D：扩展页与首页 + 非组件文字
`extensions/`（`extensionMeta` 18、`SkillsTab` 17、`McpServerCard` 16、`McpTab` 15）、`pages/Extensions`（4）、`pages/Landing`（20），以及 **`.ts` 文件里的文字**（第 8 节陷阱 1：扫描器看不见它们，约 674 个汉字，在 `store/slices/message/`、`utils/toolGrouping.ts`、`types/responseStyle.ts`、`planWorkspace/model.ts`、`usePlanSourceGuard.ts` 等）。

**顺序建议：A → B → C → D。** A 最简单，用它熟悉流程。

## 6. 单个文件怎么迁（照做即可）

### 6.1 每个文件的步骤
1. **找出所有要迁的文字。** 先看扫描器怎么数：
   ```bash
   cd web-ui
   FILE=components/layout/ChatSidebar.tsx     # 改成你在做的文件，路径相对 src/
   # 临时测试：打印该文件被扫描器识别出的文字。用环境变量传路径，不要改脚本。
   cat > src/__who.test.ts <<'EOF'
   import { readFileSync } from 'node:fs';
   import { it } from 'vitest';
   import { hardcodedText } from '@/i18n/i18n.test';
   it('who', () => console.log('WHO', JSON.stringify(hardcodedText(readFileSync('src/' + process.env.FILE, 'utf8')), null, 1)));
   EOF
   FILE="$FILE" npx vitest run src/__who.test.ts 2>&1 | grep -A80 "WHO"; rm -f src/__who.test.ts
   ```
   **扫描器只是下限，不是清单**：它看不到拼出来的字符串、对象里非 `title/label` 的字段、`.ts` 文件。**读整个文件**，把每个用户会看到的字都找出来。

   **对中文为主的文件，再做一次自检**（扫描器对这类文件漏得最多，比如 `ThinkingProcess.tsx` 里有 266 个汉字，扫描器只看到 3 处）：
   ```bash
   cd web-ui
   FILE=components/chat/ThinkingProcess.tsx     # 改成你在做的文件，路径相对 src/
   FILE="$FILE" python3 - <<'EOF'
   import os,re
   CJK=re.compile(r'[一-鿿]')
   def strip(t):
       t=re.sub(r'/\*[\s\S]*?\*/',lambda m:'\n'*m.group(0).count('\n'),t)
       return re.sub(r'(^|[^:\\])//[^\n]*',r'\1',t)
   t=strip(open('src/'+os.environ['FILE']).read())
   left=[(i+1,l.strip()) for i,l in enumerate(t.split('\n')) if CJK.search(l)]
   print(len(left),'lines still contain Chinese')
   for ln,l in left: print(ln,l[:110])
   EOF
   ```
   **迁完一个中文为主的文件后，这个数字应当接近 0**，剩下的每一行你都要能说明为什么留着（数据、注释之外的标识符、给模型看的内容）。这是比测试更严的自检，因为测试的尺子看不到它们。
2. **起键名。** 格式 `区域.含义`，如 `chatSidebar.newTask`。同一个词出现在多处，**先去语言包搜一下已有的**（`status.*`、`common.*`、`memory.type.*`），能复用就复用。
3. **写两种语言。** 在 `src/i18n/locales/en.json` 与 `zh-CN.json` **同一位置**加同一个键。
   - 原文是中文 → 中文照搬原文，英文自己写（自然、简洁，别逐字直译）。
   - 原文是英文 → 英文照搬，中文自己写。
   - 原文是半翻译残片（`loadDAGmedium`、空字符串）→ 去看历史，见第 8 节陷阱 6。
4. **改代码。** 把写死的文字换成 `t('键')`，带变量用 `{{名字}}`，**两种语言的占位符必须一致**。
5. **从基线删文件。** 迁完后，打开 `src/i18n/hardcodedBaseline.ts`，**删除该文件那一行**。
6. **跑测试：**
   ```bash
   cd web-ui
   npm run lint && npx tsc --noEmit && npx vitest run
   ```

### 6.2 `hardcodedBaseline.ts` 的规则（测试会强制）
- 一个文件的写死文字**增加** → 测试失败。
- 一个文件的写死文字**减少但没改数字** → 测试也失败（逼你把数字降下来）。所以：**迁完整个文件，直接删掉那一行**；只迁了一部分，就把数字改成扫描器现在报的数字。
- 查某文件当前被计多少处：用 6.1 的临时测试，数数组长度。

### 6.3 键是拼出来的怎么办
例如 `t(\`quality.level.${level}\`)`。编译器查不到，所以：
- 每个可能的取值都要在两个语言包里有键；
- 并且在 `src/i18n/statusLabels.test.ts` 末尾的 `cases` 数组里登记：`['quality.level.', ['satisfied', 'acceptable', 'negative', 'angry']]`。

### 6.4 语言包不是越多越好
- 一个词出现在 3 个以上地方，才抽成公共键。别为只用一次的词建公共键。
- 键名描述**含义**不是内容：`chatSidebar.newTask`，不是 `chatSidebar.newTaskText`。
- 不要翻译：品牌名（Phage-Agent、PhageScope）、产品术语 DAG/MCP/API/CPU、用户数据（计划名、任务名、模型回复内容）、后端返回的原始标识（`needs_review`、`tool_not_invoked`）。

## 7. 验收：在真实浏览器里看，两种语言各一遍

单测过了不等于页面对。每迁完一个页面/组件：

```bash
cd web-ui
VITE_DEFAULT_LANGUAGE=en VITE_API_BASE_URL= npm run build
npx vite preview --port 4173 --host 127.0.0.1 --strictPort &
```
然后用 Playwright 打开页面，**把整页文字读出来**，检查：
- 英文下：除了示例数据，**不应有中文**。
- 中文下：除了品牌/数据/标识符，**不应有英文单词**。
- 悬停、弹窗、抽屉、下拉、空状态、报错提示，**逐个点开**，不要只看首屏。
- 对话时间、相对时间（“3 分钟前”）要跟着语言变。

可参考 `web-ui/e2e/visual/` 里的 fixtures 与 `mockApi.ts`：CI 的视觉冒烟就是用固定数据在浏览器里跑，**不需要后端**。需要某个页面有数据才出现的状态时，**往 `fixtures/` 加 JSON 并在 `mockApi.ts` 的 `TABLE` 里登记**，这样 CI 也能覆盖到它。
`npm run test:visual` 会冷启动构建并跑所有页面（约 1 分钟）。

## 8. 陷阱清单（前两批踩过的坑，别再踩）

1. **扫描器只看 `.tsx`。** `.ts` 文件里的文字（store、工具函数、常量）扫描器**完全看不见**，约 674 个汉字。这是 PR D 的内容，但你在 A/B/C 里碰到某个组件调用了一个返回文字的 `.ts` 函数，也要一起迁。
2. **模块级常量里的文字不能用 hook。** 例如 `const LABELS = { done: '已完成' }` 写在文件顶层。做法：常量里存**键名**，渲染时再 `t(键)`；或者把它改成接受 `t` 的函数。参考 `pages/QualityAnalytics.tsx` 的改法。
3. **类组件不能用 hook**（`ErrorBoundary` 是类组件）。用 `translate('键')`（非 React 版本），或把文字部分拆成一个函数组件。注意：`translate` 在渲染时读当前语言，**切换语言后类组件不会自动重渲染**，对错误页可以接受，但要在 PR 说明里写。
4. **hook 必须放在提前 `return` 之前。** 组件里先 `const t = useT();`，再写 `if (!x) return null;`（React 规则，lint 会报，但别等 lint）。
5. **箭头表达式组件**（`=> ( <div>… )`）要改成块体才能放 hook：`=> { const t = useT(); return ( … ); }`。
6. **之前的整仓机器翻译留下了空白和残片。** 提交 `765efd9b`（2026-02-20）把代码里的中文机翻成英文，留下 `title=""`、`loadDAGmedium`、`memoryconnection` 这类东西。**原文在 `git show 765efd9b~1:web-ui/<路径>`**，可以查到原来的中文，用它做中文翻译的依据。
7. **有代码根据文字内容做逻辑判断，翻译会悄悄破坏它。** 已知三处，**迁到那里要特别小心，先读懂再改**：
   - `store/slices/message/index.ts:56-57`：`_looksLikeSubstantialAssistant` 用 `includes('连接中断')`、`includes('对话已中断')` 判断一段**已收到的内容**是不是“恢复提示”，决定要不要把它当成有价值的部分回复保留（第 628 行用它）。
   - `store/slices/message/index.ts:117`：`prior.startsWith('连接中断') || prior.startsWith('服务端')`，判断上一条消息是不是恢复提示，决定要不要把它拼进新内容。
   - `store/slices/message/streamHandlers.ts:115`：`normalizedLabel.includes('重试')`，用中文词判断“重试”。
   这些提示是 `store/slices/message/recovery.ts` 里 `keepPending('连接中断，…')` 等写进消息内容的。**一旦把提示文字改成按语言变化，上面这些判断对英文用户永远匹配不到**，行为就悄悄变了，而且**目前没有任何测试覆盖这条逻辑**（我查过 `_looksLikeSubstantialAssistant`、`isRecoveryNote` 在测试里一次也没出现）。
   正确做法：**不要靠文字判断，改成靠稳定标记。** 消息 `metadata` 里已经有 `recovering: true`（`recovery.ts:45`、`index.ts:85`），可以在写入提示时再加一个 `recovery_note: true`，三处判断改为读这个标记。要求：
   1. **先写测试再改**：造一条带恢复提示的消息，断言“当前行为”（中文提示被识别为恢复提示）；改完后用两种语言各断言一遍。
   2. 这一处改动**单独成一个 commit**，PR 里单独写一节说明。
   3. 拿不准就停下来问（第 9 节）。这是三处里最容易引入真 bug 的地方。
8. **后端发来的文字不要在前端翻译。** 报错、工具名、后端消息直接显示。只有前端自己写的界面文字才进语言包。
9. **用 `translate()` 的地方不会随语言切换自动刷新。** 组件里一律用 `useT()`；`translate()` 只在非 React 代码里用。
10. **复数与词序。** 中文没有复数，英文有。`{{count}} 个任务` / `{{count}} tasks`，不要写成 `{{count}} task(s)`；确实需要复数时看 i18next 的 `_one` / `_other` 后缀。
11. **别用字符串拼接组句。** `t('a') + n + t('b')` 在两种语言里词序不同。用一个带 `{{n}}` 的整句。
12. **`dayjs`**：不要再写 `dayjs.locale('zh-cn')`（#56 删过一个）。语言由 `theme/ThemeProvider.tsx` 统一设。
13. **别用 `sed -i` 批量替换。** macOS 的 sed 与 GNU 不同，之前反复出错。用 Python 精确替换并断言每处匹配次数。
14. **不要把本地文件路径、主机名写进 fixtures 或语言包。**

## 8.5 代码里还有一套“按内容猜语言”的旧机制（必须先问，别自己决定）

`utils/thinkingStream.ts`、`components/chat/ThinkingProcess.tsx`、`components/chat/job-log/useJobLogStream.ts`、`store/slices/message/streamHandlers.ts` 里有另一套中英文方案：**“思考过程”的文字语言，由最后一条消息的内容里有没有汉字来推断**（`inferThinkingLanguage`），然后在代码里写 `zh: '检索资料', en: 'Searching the web'` 两份。

它和界面语言开关是**两回事**：一个中文用户在英文部署上提问，思考过程会显示中文，界面其它部分是英文。是否合并成“跟界面语言走”，是**产品决定**，不是你来定的。
- **本任务默认：不动这套机制**，只把它里面用到的 11 个 `zh:/en:` 对保持原样。
- 如果你认为应该合并，**先停下来写下理由，问用户**（第 9 节）。

## 9. 做不了就停下来问

**停下来、写清楚问题再继续**，不要自作主张：
- 你觉得扫描器**误报**或**漏报**，想改 `i18n.test.ts` 的规则。
- 一处文字**不确定该不该翻**（比如是给模型看的 prompt，而不是给用户看的界面）。**给模型看的 prompt 和系统提示一律不翻译、不挪走**，那是后端行为，不在本任务内。
- 陷阱 7 里那类“逻辑依赖文字”的修改，你不确定改法。
- 你想把“按内容猜语言”的思考过程并入界面语言开关（第 8.5 节）。
- 需要改后端、改 `deploy/`、改 CI 配置。
- 测试因为你无法解释的原因变红。
- 某个页面你在浏览器里**无法造出数据**来看（说明哪个状态没看到，不要假装验证过）。

## 10. 提交与 PR

- 分支名 `feat/i18n-batch3-<区域>`，从 main 切；提交信息写“做了什么 + 为什么”，不要只写 `i18n`。
- 每个 PR 描述必须包含（参考 #56 的写法）：
  1. 迁了哪些文件、基线 710 → 多少；
  2. **你在浏览器里实际看了哪些页面和状态**（两种语言），**没看到的要明说**；
  3. 遇到的陷阱和你的处理；
  4. 没做的部分。
- PR 里**不要声称“已验证”除非你真的看过或测过**。写“代码路径检查过，未在浏览器里看到”是可以的，伪造验证不行。
- **不要合并 PR。** CI 全绿后交给用户，由用户说了算。CI 失败自己修；如果失败与你的改动无关（比如依赖下载超时），用 `gh run rerun --failed` 重跑一次，并在 PR 里说明。

## 11. 你可以拿来对照的东西

- 已迁完的页面：`pages/Dashboard.tsx`（最简单）、`pages/Memory.tsx`（带共享词、表格列、过滤器）、`components/memory/MemoryGraph.tsx`（带 vis-network 悬停卡片）、`pages/QualityAnalytics.tsx`（模块级常量改键的例子）。
- 带测试的例子：`components/layout/AppHeader.test.tsx`（切换语言后文字变化）、`src/i18n/statusLabels.test.ts`（运行时键）。
- 提交历史：`git log --oneline -- web-ui/src/i18n` 可以看到前几批每一步是怎么做的。
- 各 PR 描述（`gh pr view 55`、`gh pr view 56`）写了设计取舍。

## 12. 已知现状（别被它吓到）

- 迁移期间，英文部署（CityU）上会出现“英文顶栏 + 部分中文页面”，这是预期的过渡状态，不是 bug。
- 测试环境固定为中文（`src/setupTests.ts`），已有的测试断言中文；你迁移某个页面后，如果它的测试断言了中文文字，**测试里要么显式把语言设成 `zh-CN`、要么同时补英文断言**，参考 `QualityAnalytics.test.tsx`。
- 有 31 个测试文件含中文断言，迁到对应组件时一并看。
- 视觉冒烟用 `VITE_DEFAULT_LANGUAGE=en` 构建。
