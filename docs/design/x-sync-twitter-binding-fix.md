---
title: agency-identity.json 中 owner.twitter_id / owner.twitter_handle 为 null 的修复方案（云 headless 优先）
status: proposal (rev.2)
updated: 2026-04-21
owner: 调查方 (Claude, Cowork)
scope: install.sh + tagclaw-onboard.sh + refresh-agency-identity.sh + guided X sync
change-posture: 仅调查 + 设计，未改动任何生产代码
---

# 0. 部署前提（top-level framing）

**这个仓库是一键配置安装工具**，目标是在任何一台 openclaw 实例上——**通常是云服务器、batch 部署、headless、`/root/.openclaw/workspace` 路径**——低摩擦地拉起一个 self-IP Agent。本提案所有决策都必须围绕这个前提，而不是单机 dev 流程。由此三条顶层原则：

1. **非交互优先。** 默认假设没有 TTY、没有 operator 站在命令行前面。所有"需要 operator 输入某个值"的路径，必须优先支持 flag / env var / 挂载文件，再把 `[ -t 0 ] && [ -z "$FORCE_NON_INTERACTIVE" ]` 下的 prompt 当**最弱**的 fallback。install.sh / post-verify-finalize 本身运行完不能依赖 TTY。

2. **异步自愈是一等公民，不是兜底。** install 完成时 TagClaw 验证推大概率还没发，后端 `/me` 也还没有 twitter 绑定——这是**常态**，不是异常。整个方案必须把"install 跑完 → operator 异步发推 → 下一次 heartbeat 自愈 identity"这条链当成 canonical path；`post-verify-finalize` 属于这条链的一环而不是兜底。

3. **install 失败语义要对齐云语境。** 云上 install 跑完以后没人盯终端。`owner.twitter_*` null 在**刚 install 完**那一刻不是"失败"，而是"待自愈"；只有在 `status=active` 且连续 N 次 heartbeat 还没能回填的情况下，才升级为 dashboard 级 blocker。

如果某条结论与这三条原则冲突，以这三条为准。

---

# 大纲

- §1 `owner.twitter_id/handle` 生成路径与为什么是 null（**调查结论，不变**）
- §2 消费点清单与失败模式（**调查结论，不变**）
- §3 仓内 TagClaw / TagAI API 线索（**调查结论，不变**）
- §4 修复方案（围绕 §0 三条顶层原则重写）
- §5 落地顺序（围绕 §0 重写）
- §6 Multi-tenant / 分发 / 遥测（云部署语境新增）
- §7 开放问题（按云语境重答）
- 附录：关键行号速查

---

# 1. 生成路径与为什么会是 null

## 1.1 三个关键文件

- `scripts/install.sh`（2634 行）— 安装入口，编排各步骤。
- `scripts/tagclaw-onboard.sh`（714 行）— 封装 TagClaw 注册、状态轮询、post-verify 回收。
- `scripts/refresh-agency-identity.sh`（302 行）— `agency-identity.json` 的**唯一写入者**。

## 1.2 canonical 写入路径

只有 `refresh-agency-identity.sh` 可以改写 `agency-identity.json`（`scripts/refresh-agency-identity.sh:29-30` 注释里明确说 "Centralizing the write in one place"）。

关键逻辑集中在内嵌 python 块（`scripts/refresh-agency-identity.sh:95-260`）：

- 第 193-202 行从 `<workspace>/skills/tagclaw/.env` 读取
  `TAGCLAW_OWNER_TWITTER_ID` 和 `TAGCLAW_OWNER_TWITTER_HANDLE`：

      owner_twitter_id = skill_env.get("TAGCLAW_OWNER_TWITTER_ID") or ""
      owner_twitter_handle = skill_env.get("TAGCLAW_OWNER_TWITTER_HANDLE") or ""

- 第 204-211 行，只有在调用方传了 `--verify-api` 且 `.env` 里拿得到
  `TAGCLAW_API_KEY` 时，才会去 curl `https://bsc-api.tagai.fun/tagclaw/me` 并
  从 `/me` 响应里补齐：

      owner_twitter_id     = owner_twitter_id     or me.get("ownerTwitterId")     or me.get("owner_twitter_id")     or ""
      owner_twitter_handle = owner_twitter_handle or me.get("ownerTwitterHandle") or me.get("owner_twitter_handle") or ""

- 第 228-229 行如果两者最终都是空字符串，就写成 `None`（即 JSON `null`）。
- 第 248 行的 "sufficient" 判据只看 `username` 和 `eth_addr`，**不要求 twitter 字段存在**。
  因此只要 TagClaw 注册完成（拿到 username + eth_addr），`refresh-agency-identity.sh`
  就会写出一个 twitter 字段全为 null 的 agency-identity.json，并被视作"成功"。

## 1.3 谁触发 refresh，是否带 --verify-api

> 这里是本 bug 的真正死角。

| 调用点 | 文件:行 | 是否 `--verify-api` | 会跑到 `/me` 吗 |
|---|---|---|---|
| `install.sh:setup_identity` → `detect_identity` | `scripts/install.sh:262` | **是** | 会（前提是 `TAGCLAW_API_KEY` 已存在） |
| `tagclaw-onboard.sh:register_account` 注册成功后 | `scripts/tagclaw-onboard.sh:475 → 122` | **否** | 不会 |
| `tagclaw-onboard.sh:poll_status` 每次拿到新 status | `scripts/tagclaw-onboard.sh:593 → 122` | **否** | 不会 |
| `tagclaw-onboard.sh:poll_status` status = active 时 | `scripts/tagclaw-onboard.sh:600 → 122` | **否** | 不会 |
| `post-verify-finalize` 重跑 install.sh | `scripts/tagclaw-onboard.sh:641` | **是**（经由 install.sh） | 会 |
| 三个 cycle 脚本 (`main-heartbeat.sh` / `bookmarker-cycle.sh` / `trader-cycle.sh`) | — | — | **完全不调用 refresh** |

结论：**把 /me 里的 `ownerTwitterId` / `ownerTwitterHandle` 回写到 identity JSON 的唯一机会，就是 install.sh 自己那一次 `--verify-api`，以及显式跑 `post-verify-finalize` 时重入 install.sh。** 其它任何流程，包括 cron 定时轮换，都不会再次触发 /me 取 twitter 字段。这条死角在云 headless 场景下尤其致命——operator 大概率不会手动回来重跑，需要靠 heartbeat 自愈来闭环（参 §4）。

## 1.4 `tagclaw-onboard.sh:register_account` 写入的 skill .env 字段

参考 `scripts/tagclaw-onboard.sh:445-466`，register 后写入 `.env` 的 key 只有：

- `TAGCLAW_AGENT_NAME`
- `TAGCLAW_AGENT_USERNAME`
- `TAGCLAW_AGENT_DESCRIPTION`
- `TAGCLAW_API_KEY`
- `TAGCLAW_VERIFICATION_CODE`
- `TAGCLAW_STATUS`
- `TAGCLAW_ETH_ADDR`
- `TAGCLAW_WALLET_DIR`
- `TAGCLAW_PROFILE_URL`
- `TAGCLAW_API_BASE`

**没有 `TAGCLAW_OWNER_TWITTER_ID` / `TAGCLAW_OWNER_TWITTER_HANDLE`**。`poll_status`
（`scripts/tagclaw-onboard.sh:581-590`）也只更新 `TAGCLAW_STATUS` / `TAGCLAW_AGENT_USERNAME`
/ `TAGCLAW_PROFILE_URL`。所以只要没有 TagClaw 后端主动把这两个字段随 /me 返回，
`.env` 永远不会有它们，refresh 的第一层来源永远拿不到值。

## 1.5 `/me` 响应契约

`scripts/test_me_shape_normalization_v1.py:32-39` 明确了一个 fixture：

```
AGENT_FIXTURE = {
    "username": "selfipbot",
    "ethAddr": "0xe9...",
    "ownerTwitterId": "1863098517117702145",
    "ownerTwitterHandle": "thefandotfun",
    ...
}
```

`adapters/tagclaw.py:54-78` 的 `extract_me_agent()` 把四种历史 envelope
(`{agent:{}}`, `{data:{agent:{}}}`, `{data:{...}}`, `{...}`) 统一解到内层 agent dict。
refresh 脚本自己在第 179-190 行也重复了一版同样的 unwrap 逻辑，字段映射也对（camelCase 和 snake_case 两种都认）。

> 也就是说：一旦 `/me` 真的返回 `ownerTwitterId` / `ownerTwitterHandle`，
> refresh 路径能正确落到 identity JSON 里。**但前提是** `/me` 后端愿意给这两个字段。
> 这个前提就是本方案的"信任锚"，在云上必须靠 CI 契约测试钉死（见 §7-#1）。

## 1.6 本机实际产物对照

仓内 `.install-next-steps.json` 和 `.installed` 显示本次安装状态：

- `tagclaw_onboard_status: "skipped"`
- `identity_resolved: false`
- `credentials_exist: false`
- `x_tweets_seed_status: "failed"`

也就是说**用户这一次根本没跑 TagClaw 注册**（或者没跑完），skill `.env` 里没有
`TAGCLAW_API_KEY`、没有 `TAGCLAW_AGENT_USERNAME`、更没有 twitter 字段；
`refresh-agency-identity.sh` 会在第 266-270 行 `exit 2`（"Identity sources incomplete"），
连 identity JSON 都不会被重写，所以 `config/agency-identity.json` 仍然是模板初始值（全 null）。
这就是"两个字段都为 null"的最小再现。

云 `/root/.openclaw/workspace/` 没挂到我这边，所以本次没法直读那份 workspace 副本，但根据
`install.sh:443-444` 的 `cp` 行为：如果仓内副本是 null 模板，workspace 那边也一定是 null。

## 1.7 即便用户之后完成了 TagClaw 注册，也可能仍为 null

- 用户 run 了 `install.sh` 但没 run `tagclaw-onboard.sh full`（本次情况）——skill `.env` 根本没建立，refresh 早退。
- 用户 run 了 `full`、发了验证推、status 转 active，但没跑 `post-verify-finalize`——onboarding 内部的几次 refresh 都**没加 --verify-api**，不会触发 /me，`.env` 里又没有 twitter key，**identity JSON 永远不会被回填**。云上这种"跑完就没人回来"的情况是**默认状态**。
- `/me` 后端即使在 active 状态下也没返回 `ownerTwitterId`/`ownerTwitterHandle`——仓内缺乏能证伪这点的在线契约文档，只能从 `test_me_shape_normalization_v1.py` 的 fixture 推断"应该会返回"。

---

# 2. 消费点清单 & 失败模式

全仓搜 `twitter_id` / `twitter_handle` / `ownerTwitter*` 的结果，排除 `_worktrees/` 和 `.claude/worktrees/`：

## 2.1 真正会因为 null 而改变行为的消费点

| 消费点 | 文件:行 | 读法 | 字段缺失时的失败模式 |
|---|---|---|---|
| 引导式 X 拉推 | `scripts/sync_guided_x_tweets.py:47-57` | `handle = owner.get('twitter_handle') or owner.get('twitter_id')` | `load_identity_handle()` 返回 `None` → `run_sync()` 第 93-95 行 `status='blocked', blockers=['missing_owner_twitter_handle']`。**这正是用户看到的 X 推文同步失败。** |
| wiki contract 自检 | `scripts/verify_wiki_contract.py:135, 245` | 同上 | 返回 `'owner.twitter_handle missing; guided X sync not yet configured'`；`missing_owner_twitter_handle — X sync cannot proceed`。非致命。 |
| bookmarker align 子指标 | `scripts/run_bookmarker_runtime_v1.py:674-678, 756` | `owner_twitter_id = owner.get('twitter_id')` / `owner_username = owner.get('twitter_handle').lstrip('@')` | `_compute_align_via_api()`（第 600 行）在两者都空时直接返回 `(None, "no-owner-binding")`，TAS_social 的 align 分项写成 0，community 分项照常。**这是"首次轮换部分失败"里 bookmarker 那一路的非致命退化**。 |
| 模板渲染 | `scripts/install.sh:302, 322, 337` | `d['owner']['twitter_handle'] or d['owner']['twitter_id']` | 空时 `twitter_handle="unknown"`，再灌到 `agents/*.md` 和 `dashboard/index.html`。对运行无影响，只是文案写成 `Owner: unknown`。 |

## 2.2 纯文档、纯 config 模板、纯测试 fixtures

列出备查但不会独立引发失败：`config/config.template.json:15-16`、`config/agency-identity.json:10-11`、`SKILL.md:42`、`docs/guided-x-sync-remediation-plan.md`、`docs/x-setup.md`、`scripts/test_guided_x_sync_v1.py:97`、`scripts/test_me_shape_normalization_v1.py:35-83`、`adapters/tagclaw.py:54-78`。

## 2.3 dashboard 不直接消费 owner.twitter_*

`dashboard/server.py:177-209` 只读 `identity.agent.username` 和 `agent.profile_url`，以及 skill .env 的 `TAGCLAW_AGENT_USERNAME`。和 owner.twitter 无关，不会因为 null 出现 500。

## 2.4 install-first-cycle 的"首次轮换部分失败"落在哪里

按 `scripts/install.sh:1659-1672` 的编排：安装完核心步骤后会先做一次 bootstrap
`sync_guided_x_tweets()`，owner.twitter_handle 为 null 时该次直接返回 `status=blocked`（参 2.1 第 1 行），这就对应用户报告里的"首次轮换部分失败，cron 会自动重试"。cron 之后每次重试的也是同一个脚本，同样的 handle 缺失 → 同样 blocked。**cron 重试跑同一个 script 修不了这个问题**——修复链路必须由 heartbeat 去触发 identity refresh，而不是盲目重跑 sync（见 §4）。

---

# 3. 仓内的 TagClaw / TagAI API 线索

## 3.1 已找到的端点

| 端点 | base URL | 鉴权 | 在仓内何处使用 | 与 twitter 绑定相关的字段 |
|---|---|---|---|---|
| `GET /tagclaw/me` | `https://bsc-api.tagai.fun` | `Authorization: Bearer $TAGCLAW_API_KEY` | `adapters/tagclaw.py:226`, `scripts/refresh-agency-identity.sh:161`, `scripts/run_trader_runtime_v1.py:153`, `scripts/build_main_input_packet_v2.py:136`, `dashboard/server.py:79` | 按 fixture **应包含** `ownerTwitterId` / `ownerTwitterHandle`；仓内未见在线 schema 证实 |
| `POST /tagclaw/register` | 同上 | 无鉴权（提交 ethAddr+steem keys） | `scripts/tagclaw-onboard.sh:406` | 响应：`apiKey` / `verificationCode` / `username` / `ethAddr` / `profileUrl`，**没有 twitter 字段** |
| `GET /tagclaw/status` | 同上 | Bearer | `scripts/tagclaw-onboard.sh:537` | 已解析 `status` / `username` / `profileUrl`；**未解析 twitter 字段**（即便后端可能带） |
| `GET /curation/tweetCurateList?tweetId=X` | `https://bsc-api.tagai.fun/curation` | Bearer | `scripts/run_bookmarker_runtime_v1.py:610-612` | 参与者记录里有 `twitterId` / `twitterUsername` |
| `GET /curation/getReplyOfTweet?tweetId=X&pages=0` | 同上 | Bearer | `scripts/run_bookmarker_runtime_v1.py:631-633` | 同上 |
| `GET /tagclaw/feed` / `/feed/me` | `https://bsc-api.tagai.fun/tagclaw` | Bearer | `adapters/tagclaw.py:214`, `scripts/compute_tas_social_v2.py:13-14` | 帖子级，非绑定 |
| `GET /tiptag/getETHPrice` | `https://bsc-api.tagai.fun` | 公开 | `scripts/run_trader_runtime_v1.py:380` | 无关 |

## 3.2 没找到的东西

仓内 **未找到**以下线索：专用的 Twitter binding / unbinding API；`bound_twitter` / `binding` / `twitter_binding` 字段；仓内保留的 `/tagclaw/SKILL.md` 或 `/tagclaw/REGISTER.md`（它们是 `curl` 远拉的，`scripts/tagclaw-onboard.sh:252-257`）。

## 3.3 结论

- 查询绑定 Twitter 的**唯一仓内已知 endpoint** 是 `GET /tagclaw/me`。
- 字段名：`ownerTwitterId` / `ownerTwitterHandle`（含 snake_case 兼容项）。
- 鉴权：`Authorization: Bearer <TAGCLAW_API_KEY>`。
- 如果 `/me` 本身不回这俩字段，仓内代码没有任何替代路径。

---

# 4. 修复方案（云 headless 优先）

## 4.1 修复目标（按 §0 原则改写）

在云 headless 部署中：

- `install.sh` 跑完后即便 `owner.twitter_*` 还是 null，也**不是失败**。安装进程退出码为 0，只在 next-steps 里标注"binding unresolved, will auto-heal"。
- 后续 TagClaw 从 pending 转 active 的状态跃迁（operator 从另一个设备发了验证推），必须由**本地的 heartbeat 自愈链**负责回写 identity JSON，不需要 operator 再登机器。
- 所有"需要 operator 输入值"的路径，必须支持非交互方式一次性预注入（flag / env / 挂载文件）；TTY prompt 只保留给真·本地 dev 场景。
- 连续 N 次 heartbeat 仍回填失败（status=active 且 /me 无 twitter 字段）——**这才**升级为 dashboard blocker，并给 operator 一个可通过 HTTP 或文件 watch 接收到的通知。

## 4.2 Binding 真相源优先级（重排）

按云 headless 优先、`/me` 作 canonical：

1. **TagClaw `/me` 返回的 `ownerTwitterId` + `ownerTwitterHandle`（canonical，首选）**
   - 云部署的零摩擦路径：register → operator 异步发推 → 后端完成绑定 → `/me` 变得可用。
   - 来源标记 `binding_source="tagclaw.me.verified"`, `verified=true`。
   - 这是方案唯一信任锚，在 CI 里钉死（见 §7-#1）。
2. **非交互预注入的 operator 声明**
   - flag: `bash scripts/install.sh --owner-twitter-handle=foo --owner-twitter-id=123`
   - env: `OWNER_TWITTER_HANDLE=foo OWNER_TWITTER_ID=123 bash scripts/install.sh`
   - 挂载文件：`config/owner.local.json`（gitignore），schema 如 `{"owner":{"twitter_handle":"foo","twitter_id":"123"}}`；批量部署 / IaC 的推荐方式。
   - 这些来源写进 skill `.env` 的 `TAGCLAW_OWNER_TWITTER_HANDLE` / `TAGCLAW_OWNER_TWITTER_ID`（参 §1.2 已有兼容点）。
   - `binding_source="operator.declared"`, `verified=false`，直到某次 `/me` 确认后升级为 `verified=true`。
   - 语义："我确定绑定关系是这个，只是 /me 还没来得及刷新"——短暂态兜底，不是长期真相。
3. **TTY 交互 prompt（最弱 fallback）**
   - 只在 `[ -t 0 ]` **且** `FORCE_NON_INTERACTIVE` 未设 **且** 上面两级都没值时触发。
   - 云 headless 下默认不会触发（root 跑脚本 stdin 通常非 tty，或直接被 `FORCE_NON_INTERACTIVE` 标记）。
   - 结果写进 skill `.env` 同上，`binding_source="operator.declared-tty"`。
4. **guided-x-urls.json（条件性路径，建议不放进默认 fallback）**
   - 现状：这个 manifest 的 canonical 语义是"browser-guided 会话生成的推文 URL 列表"，不是 "twitter binding 声明"。
   - 云部署难点：这个工件**得 operator 在本地浏览器里跑完产生**，然后怎么送到 `/root/.openclaw/workspace/runtime/shared/guided-x-urls.json`？目前仓内**没有**任何上传/同步通道（没 HTTP endpoint、没上传器、没 cowork bridge）。operator 唯一可行方式是 `scp` 或粘到某个预先配好的 HTTP 服务器，属于 out-of-band，不应默认化。
   - 建议：在**本修复**里不使用 guided-x-urls.json 反解 handle 作 fallback；如果 manifest 存在就只当 URL 源使用，与 binding 解耦。若后续要把它作为 binding fallback，先另立 issue 定义"如何把 local artifact 带进 cloud workspace"（scp? TagClaw 侧上传？cowork 同步？），**不要在本 fix 里假设这个通道存在**。
5. **全部失败 → 保持 null，emit 明确 next-step，但 install.sh 正常退出 0。** 不降级 install_status 至 `failed`；heartbeat 接手自愈（见 §4.4）。

> 排除了原 v1 提案里把"operator 手工输入"放进链首的写法。云 headless 下手工输入既打不进来，也不是 `/me` 可以替代的。

## 4.3 post-verify-finalize 作为一等公民

把它从"用户手动跑一次"升格为**自动触发**，触发点有三层，按优先级：

### 4.3.1 触发点 1：install.sh 留下的 systemd oneshot / cron `@reboot`（首选）

- install.sh 最后在 `/etc/systemd/system/` 或 workspace 自有的 user-level timer 里注册一个 `self-ip-finalize.service`（oneshot），命令即 `bash <workspace>/scripts/tagclaw-onboard.sh post-verify-finalize --workspace <workspace>`。
- 同步注册一个 `self-ip-finalize.timer`，`OnCalendar=*:0/5` 持续试；一旦 status=active 且 identity 回填成功，自删 timer（oneshot 行为）。
- 为什么首选：云上 systemd 是标准件，不依赖 openclaw 自己的 cron 调度器。
- 注意：`scripts/install.sh` 不能假设 root 权限（云上 root 常见但不保证）；若无 systemd 写入权限，退到触发点 2。

### 4.3.2 触发点 2：heartbeat cron 扫描（fallback，主力）

- `scripts/main-heartbeat.sh` 每次运行时增加一步：读 `<workspace>/skills/tagclaw/.env` 的 `TAGCLAW_STATUS`，若 `=active` **且** identity.owner.twitter_handle 为 null **且** `<workspace>/runtime/shared/identity-sync.json` 显示"还没同步"或"上次同步>=T 分钟前"，则触发一次 `refresh-agency-identity.sh --workspace <ws> --verify-api`。
- 成功写回 → 更新 `identity-sync.json:{last_synced_at, source:"tagclaw.me.verified", verified:true}`，下次就不再打 /me。
- 失败或 /me 还没回字段 → `identity-sync.json:{last_attempt_at, attempts+=1, last_error:"me_missing_twitter"}`，继续等下一次 heartbeat，直到 attempts 达阈值（建议 6 次 = 若 heartbeat 周期 5min 即 30min）再升级为 dashboard blocker（见 §4.5）。
- 这条路径**不走** `post-verify-finalize` 的重入 install.sh 逻辑——因为那一步还会重跑 cron 注册、dashboard 启动等重任务，不适合 heartbeat 频率。只调用最小的 refresh 即可。

### 4.3.3 触发点 3：operator 手动（文档化，但非依赖）

- 保留 `bash scripts/tagclaw-onboard.sh post-verify-finalize` 的原有入口，文档里明确"只在 1/2 都异常时才需要手动跑"。

### 4.3.4 状态跃迁信号与幂等

- 判据：`TAGCLAW_STATUS` 从任意其它值转 `active`——但云上 heartbeat 不保留历史，只比较"当前 active + identity 仍 null"。
- 幂等：靠 `runtime/shared/identity-sync.json` 的 `verified:true` 作旗标，一旦翻 true 就不再打 /me。
- 节流：同一文件里记 `last_attempt_at`，heartbeat 内若距上次 <60s 则跳过本轮（防 cron 同机并发）。

### 4.3.5 操作者通知（云上没人盯终端）

按难度从低到高，建议至少做前两个：

- **文件事件**：成功回填后写 `<workspace>/runtime/shared/events/owner-binding-resolved.json`，带 `{timestamp, twitter_handle, twitter_id, source}`。dashboard 可以订阅这个目录做"最近绑定成功"浮层。
- **dashboard state**：`dashboard-service.json` / `/api/status` 接口暴露一个 `owner_binding.status = "resolved" | "pending" | "stalled"` 字段，frontend 显式显示。
- **webhook**（可选）：`config/agency.config.yaml` 增加一个 `notifications.webhook_url`，发一次 POST；云部署 Operator 可以把这个挂到 Slack/飞书/企业微信。本修复不强制实现，留 hook。
- **TTY 场景**：`echo` 一行到 stdout/stderr——只在 `install.sh` 本次还活着时有用，云上几乎不相关。

## 4.4 Cron heartbeat 作为主修复载体

**重定位**：在云 headless 场景下，heartbeat 自愈是**主要**修复路径，不是兜底（tagclaw-onboard 内部 refresh 那条路径在 §1.3 已经证明是死的，靠不住）。

- **触发频率**：每次 `main-heartbeat.sh` 执行都检查（见 §4.3.2），不新增独立 cron 项，避免和 PR #5/#6 的 scheduler 可达性探测冲突（后者测的是 `openclaw cron list/health` 的探活，和 /me 没关系）。
- **节流**：单次 heartbeat 最多发 1 次 /me；`identity-sync.json` 记 `last_attempt_at` 和 `attempts`。命中 `verified:true` 之后不再触发，直到某种失效（如 `TAGCLAW_STATUS` 从 active 回退到非 active，极少见）。
- **幂等**：多个 heartbeat 并发（罕见但理论可能）靠 `identity-sync.json` 的原子 write + `flock`（`lib/common.sh` 可复用已有 atomic_write_json）。
- **错误隔离**：`/me` 抖动 / 401 / 500 等**一律不算 scheduler 错误**，也不算 heartbeat 失败。独立记录在 `identity-sync.json.last_error`，不污染主 heartbeat stdout 的 `HEARTBEAT_STATUS` 字段（`docs/main-heartbeat-contract.md`）。文档里要一条显式声明"`identity-sync` 错误不进 `HEARTBEAT_STATUS`，不触发 scheduler unreachable"。
- **开关**：`config/agency.config.yaml` 加 `identity.self_heal.enabled: true` 默认开，operator 可关（用于 /me 配额担心、或 binding 字段后端永不返回场景）。关闭时 heartbeat 跳过整个 identity 探测，不打 /me。

## 4.5 install.sh 失败态 UX（云 headless 语义）

- **刚安装完成那一刻，`owner.twitter_*` 全 null 不算失败。** install.sh 的 `install_status` 不应因为 handle 缺失降为 `partial`——这条判据（`install.sh:1695-1700`）保持当前形式，**不加** `OWNER_TWITTER_HANDLE_SET` 这种硬门槛（原 v1 提案里的这条建议撤回）。
- **next-steps 改成"将自愈"语气**：目前 `install.sh:1806-1809` 的 `guided_x_sync` step 文本建议操作者去"配置 handle + 跑 sync 脚本"。改成分叉：
  - 如果 `OWNER_TWITTER_HANDLE` 已通过 flag/env/owner.local.json 预注入 → 直接写入 identity，emit step `binding_source=operator.declared, verified=false, will be upgraded on next /me success`。
  - 如果没预注入 → emit step `owner_binding_pending`，文案类似："Owner X binding not yet resolved. Will auto-heal on next TagClaw status sync. Optional manual path: ..."；`auto_dispatchable:true` 因为 heartbeat 会自愈。
- **summary box**：`install.sh:2501-2510` 加一行 `Owner X binding: verified | declared(pending-verify) | pending (auto-heal)`，让 operator 即便只 tail install 日志也能看到状态。
- **sync_guided_x_tweets.py 的语义小改**（必须和本方案一起做，否则 first cycle 还是会被标 blocked 而 cron 重试无效）：当 `load_identity_handle()` 返回 None 且 `identity-sync.json` 存在且显示"self_heal in progress"时，`status` 应为 `deferred` 而非 `blocked`，`blockers=[]`，新增 `deferrals=['awaiting_owner_binding_autoheal']`。这样 X 同步的 `failed/blocked` 语义保留给真的不可恢复错误；"等 binding 自愈中"不污染 installer 的失败计数。
- 不在 install.sh 里 prompt 用户。任何 interactive 路径只在 `[ -t 0 ] && [ -z "$FORCE_NON_INTERACTIVE" ]` 条件下启用，且是最末端 fallback（§4.2#3）。

## 4.6 写入契约（保 schema v1 不变）

- `agency-identity.json` 顶层 schema `agency.identity.v1` **不变**。
- `owner` 对象下**可选新增**三键：`binding_source` / `verified` / `last_verified_at`。所有现有消费点（§2）只用 `.get('twitter_handle'/'twitter_id')`，多塞键不破。
- `runtime/shared/identity-sync.json`（新）：单文件，schema `identity.sync.v1`：

      {
        "schema": "identity.sync.v1",
        "last_attempt_at": "2026-04-21T10:00:00Z",
        "last_success_at": null,
        "attempts": 0,
        "verified": false,
        "last_error": null,
        "source": null
      }

- 写入唯一通过 `refresh-agency-identity.sh`，保持 §1.2 的 "single writer" 契约；`identity-sync.json` 由 heartbeat 自身写，但逻辑尽量下沉到 `lib/common.sh` 共享函数。

## 4.7 与 PR #5/#6 修过的 cron 重试逻辑是否冲突

相关 CHANGELOG 条目：

- `v2.1.3` 自动尝试 cron 注册（clawdi 云）
- `v2.3.1` `scripts/finalize-crons.sh` + scheduler reachability retries
- `v2.5.1` 多信号探测（`cron list` + `health --json` + `cron status`）统一到 `lib/common.sh:probe_scheduler_reachable`

与本提案的正交点：

- 它们管的是 **openclaw scheduler 探活 + cron 注册**；本提案管的是 **TagClaw API binding 回填**，两个不同上游，网络错误路径独立。
- heartbeat 自愈路径**不共享**这些探测——它走 `curl` 到 `bsc-api.tagai.fun/tagclaw/me`，不经 openclaw cron 命令。因此 `probe_scheduler_reachable` 不受影响，反之亦然。
- `/me` 抖动不应进入 `_PROBE_RESULT`、不应触发 `scheduler_unreachable` 的诊断——在 `lib/common.sh` 的新 identity 函数注释里显式写明这一点。
- `install_status=verified` 判据（`install.sh:1695-1700`）保持现状（不加 handle 必需条件），避免回归那些"identity 暂缺但其它都正常"的已部署 agent。

---

# 5. 推荐落地顺序（云 headless 优先）

1. **最低成本收敛**：修 `tagclaw-onboard.sh:refresh_identity()`（`scripts/tagclaw-onboard.sh:122`），所有调用路径都加 `--verify-api`——register 成功后、poll_status 每次新 status、status=active 时。这一步不引入新概念，但把 §1.3 表格里三行"否"变成"是"，直接修掉"用户跑完 full 但 identity 仍 null"。
2. **接收非交互预注入**：在 `install.sh` 的 arg parsing 加 `--owner-twitter-handle` / `--owner-twitter-id`，同时读 `OWNER_TWITTER_HANDLE` / `OWNER_TWITTER_ID` env var，以及解析 `config/owner.local.json`（gitignored）。任一存在即写进 `<workspace>/skills/tagclaw/.env` 的 `TAGCLAW_OWNER_TWITTER_HANDLE` / `TAGCLAW_OWNER_TWITTER_ID`，让后续所有 refresh 路径都能读到。
3. **heartbeat 自愈**：`scripts/main-heartbeat.sh` 增加识别 + 节流 + 单次 refresh 调用；新增 `runtime/shared/identity-sync.json` 格式；`lib/common.sh` 增加共享函数 `should_retry_identity_sync` / `record_identity_sync_result`。配置开关 `identity.self_heal.enabled`。
4. **sync_guided_x_tweets.py 语义微调**：binding 仍为 null 但 self-heal 在进行中时，返回 `status=deferred` 而非 `blocked`，消除 first-cycle 假失败。
5. **systemd oneshot / @reboot timer（可选）**：install.sh 尝试写 `self-ip-finalize.timer`；若权限不足或非 systemd 宿主，logs 降级到触发点 2（heartbeat-only），但不报错。
6. **Schema 扩展（向后兼容）**：`refresh-agency-identity.sh` 允许在 `owner` 下写 `binding_source` / `verified` / `last_verified_at`。
7. **Install next-steps 重写文案**：从"请配置 handle"改成"will auto-heal"，保 install.sh exit 0。
8. **Dashboard**：`/api/status` 加 `owner_binding` 块；frontend 显示；`runtime/shared/events/owner-binding-resolved.json` 写入。
9. **CI 契约测试**（§7-#1）：新增测试 pin `GET /tagclaw/me` 在 active 账户下必回 `ownerTwitterId` + `ownerTwitterHandle`——用 staging TagClaw 账号定期跑；失败即阻断发版，因为整个自愈链的信任锚就在这里。
10. **可选：webhook 通知**（§4.3.5）。

---

# 6. Multi-tenant / 分发 / 遥测（云部署语境新增）

云 / 批量场景下新增的考量，本修复不全部覆盖，但要列清楚免得以后踩。

## 6.1 一台云盒子上多 agent 实例

- 当前仓架构假设 `<workspace>` 即 `$HOME/.openclaw/workspace`，且 workspace 一对一对应一个 agent。如果同机跑多个 agent（不同 workspace 路径），`OWNER_TWITTER_HANDLE` env var 是**全局的**，直接用会冲突。
- 推荐做法：**不要**让 `OWNER_TWITTER_HANDLE` 成为唯一注入通道——用 `--owner-twitter-handle` flag（作用域就是本次 install 调用）或 `config/owner.local.json`（作用域就是 repo checkout）兜底。批量部署脚本应为每个实例**单独 repo checkout**或**单独 workspace dir**，并分别传入各自的 flag 值。`docs/batch-self-ip-agent-runbook.md` 已经接近这个模式，值得在本提案落地时同步更新运行手册。
- `TAGCLAW_OWNER_TWITTER_HANDLE` 写进 `<workspace>/skills/tagclaw/.env` 天然按 workspace 隔离，没有冲突。

## 6.2 TagClaw API key 的分发路径

- 当前流程靠 `tagclaw-onboard.sh` 内部跑 `/register` 拿 `apiKey`，存 `<workspace>/skills/tagclaw/.env`（`scripts/tagclaw-onboard.sh:455`）。这对**新建 agent** 够用。
- 复用既有 TagClaw 账号（批量、预先注册）时，operator 需要把 api key 注入到云：
  - env：`TAGCLAW_API_KEY=... bash scripts/install.sh` —— 当前 `install.sh:126-148` 只从 `.env` 读，没消费 env；需要补一层 env → .env 的兜底。
  - 挂载：`config/tagclaw.local.json` gitignored，schema `{"api_key":"...","agent_username":"..."}`。
  - IaC：Terraform / Ansible 等自动化把 api key 用 secrets manager 写入 `.env`。
- TagAI 登录 token 换 API key 的通道仓内**未找到**，如需此路径要先和 TagClaw 后端确认是否暴露。
- 本修复**不强制**实现上述分发优化，但 Schema 和入口要对齐，以免后续加时互相覆盖。

## 6.3 跨多个云部署的遥测

- 目标：回答"有百分之几的 installer 卡在 `owner.twitter_*` null"。
- 现状：install.sh 写 `.installed` / `.install-next-steps.json` 到本机 workspace，没有上报通道。
- 建议（独立议题，不阻塞本修复）：新增 `config/agency.config.yaml:telemetry.install_report_url` 可选项，install 完成后把 `.install-next-steps.json` 的摘要（匿名化）POST 到一个 operator 自建 endpoint；默认关闭。
- 本修复只需把 `owner_binding.status` 加到 `.install-next-steps.json` 顶层，为将来遥测做好字段。

## 6.4 工件从 local 带到 cloud

- `guided-x-urls.json`、`owner.local.json`、`tagclaw.local.json` 这类 operator 在本地生成然后要带上云的文件，**目前仓内没有通道**。默认方式是 `scp` 或镜像内预置。
- 本修复里凡是依赖这些工件的路径（§4.2#2 的 `owner.local.json`）都要在文档里说清楚"这是 IaC/镜像职责，不是 installer 职责"。
- 未来如果要把 `guided-x-urls.json` 变成 binding fallback，先做一个独立的上传通道议题，不要在这里捎带。

---

# 7. 开放问题（按云 headless 语境重答）

1. **`/me` 在验证推文发布后，后端是否一定返回 `ownerTwitterId` + `ownerTwitterHandle`？（提升为发布阻塞级问题）**
   - 仓内的 `test_me_shape_normalization_v1.py:32-83` 只是本地 mock fixture。
   - 云 headless 自愈链**全部信任**这个响应；如果后端不回，heartbeat 自愈链是空转。
   - 必须做的事：在 CI 里新增一个契约测试，用 staging TagClaw 账号（已绑定 Twitter、status=active）定期拉 `/me`，断言 `ownerTwitterId` 和 `ownerTwitterHandle` 非空。测试失败就不发 installer 新版。
   - 不能靠"每次 operator 自己 curl 一下" —— 云上那位 operator 根本不 curl。

2. **非交互 flag / env / `owner.local.json` —— 从"可选增强"升级为"方案前提"。**
   - 必须提供至少 flag 和 env 两种入口；`owner.local.json` 文件挂载作为 IaC 推荐方式。
   - TTY prompt 只在真·本地 dev（`[ -t 0 ] && ! FORCE_NON_INTERACTIVE`）下启用，生产云部署默认走不到。
   - 待确认：`FORCE_NON_INTERACTIVE` 的开关名是否要和 openclaw 既有某个约定对齐（仓内未搜到现成约定，需 user 决定命名，或取 `CI`/`DEBIAN_FRONTEND=noninteractive` 通行标志之一）。

3. **`binding_source` / `verified` / `last_verified_at` 三键能否写到 `owner` 下？**
   - 顶层 schema 版本号 v1 不变，但严格 schema 消费者可能 warning。
   - 备选：落到同级的 `runtime/shared/identity-binding.json` 单独文件，保 identity JSON 干净。
   - 用户倾向？

4. **heartbeat 自愈频率上限 & 退避策略**
   - 建议：每次 heartbeat 最多 1 次 /me；连续失败 6 次（≈30min）升级 dashboard blocker；升级后每小时再试 1 次直至成功或 operator 显式关闭 self_heal。
   - 用户是否接受这个阈值，以及**是否对 /me 配额敏感**——若敏感，默认关闭 self_heal，只由 operator 显式开。

5. **systemd oneshot timer 是否允许 installer 写？**
   - 云盒子 root 权限情况不一定相同；写 `/etc/systemd/system/` 可能越权。
   - 是否接受 installer 只尝试用户级 `~/.config/systemd/user/`，失败则降级为"仅 heartbeat 自愈"？

6. **multi-agent per host 边界**
   - 仓是否要在本修复里明确"一台机器一个 agent"的假设，并在 install.sh 初期检测同 host 上是否已有其它 workspace、若有则拒绝？还是维持"允许多实例，靠 workspace 路径隔离"的现状？
   - 与 `docs/batch-self-ip-agent-runbook.md` 的定位需要 user 确认。

7. **TagAI 登录 token 换 API key 的通道**
   - 云批量场景需要，但仓内无线索。本修复是否顺便加一个 `config/tagclaw.local.json` 注入路径（预置 api_key），还是纯靠 onboard 流程？

8. **失败遥测 endpoint**
   - 是否接受在 `config/agency.config.yaml` 预留 `telemetry.install_report_url` 字段（默认关），为将来汇总"安装卡 binding null 比例"做准备？

9. **是否要求 handle 活性校验（对外 HEAD 检查）**
   - 仍倾向"不引入"（破坏 adapters stdlib-only 规则，增加外网依赖）；`/me` 回来即视作 verified。
   - 用户若要更强保证，可加可关开关。

10. **发推人 vs `owner.twitter_handle` 必须同一人吗**
    - 业务层面可能不一致（代运营）。
    - 当前仓代码默认同一；本修复**不扩展**，另做议题。

---

# 附录：关键行号速查

- `scripts/refresh-agency-identity.sh:200-211, 228-229` — owner.twitter_* 写入逻辑
- `scripts/refresh-agency-identity.sh:266-270` — "sources incomplete" 早退
- `scripts/install.sh:262` — **唯一**带 `--verify-api` 的调用
- `scripts/tagclaw-onboard.sh:122` — 内部 refresh 调用（**无 --verify-api，本 bug 的死角**）
- `scripts/tagclaw-onboard.sh:445-466` — register 写入的 .env key（**不含 twitter**）
- `scripts/tagclaw-onboard.sh:581-590` — poll 写入的 .env key（**不含 twitter**）
- `scripts/sync_guided_x_tweets.py:47-57, 93-95` — 消费端，null 直接 blocked
- `scripts/run_bookmarker_runtime_v1.py:674-678, 625-627` — 消费端，支持 username 单分支
- `scripts/verify_wiki_contract.py:135, 245` — 消费端，只做自检报告
- `adapters/tagclaw.py:54-78, 226` — `/me` envelope 归一化 + 调用点
- `scripts/test_me_shape_normalization_v1.py:32-83` — `/me` 契约回归测试 fixture
- `config/agency-identity.json:10-11` — 初始模板（全 null）
- `.install-next-steps.json:22, 66-70` — 本机生成物，`x_tweets_seed_status=failed`, `sync_command_failed`
- `scripts/install.sh:1659-1672` — 安装编排主循环 + first bootstrap sync
- `scripts/install.sh:1695-1700` — `install_status=verified` 判据（本提案不加 handle 必需项）
- `scripts/install.sh:1806-1809` — 当前 `guided_x_sync` next-step（本提案改文案）
- `docs/main-heartbeat-contract.md` — heartbeat stdout 契约（本提案新增字段不进 `HEARTBEAT_STATUS`）
