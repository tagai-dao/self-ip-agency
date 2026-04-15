/* TagClawX Dashboard — app.js — v20260410d Dashboard V2 + NOC + zh i18n + Claude Dispatch tab */
'use strict';

// ── i18n ───────────────────────────────────────────────────────────────────
const I18N = {
  zh: {
    subtitle: '智能体看板',
    refresh: '↻ 刷新',
    // Bootstrap banner
    'bootstrap-title': '🔵 环境刚刚安装 — 等待首次智能体运行',
    'bootstrap-hint': '首次运行主心跳、书签员和交易员周期后，数据将自动填充。下方所有指标目前处于引导/待定状态。',
    // Section titles
    'section-tas-command': '🎯 TAS 指挥中心',
    'col-tas-social': 'TAS_social',
    'col-tas-aggregate': '聚合 TAS',
    'col-tas-trade': 'TAS_trade',
    'section-autoresearch': '🔬 AutoResearch 进化环路',
    'section-agent-details': '🤖 智能体详情',
    'section-wiki-intel': '📚 self-IP LLM Wiki — 知识层',
    'section-timeline': '⏱ 行动时间轴',
    'section-operator-lanes': '🔄 操作面板',
    'panel-dev': '🔨 鲁班 / Claude 调度',
    // Agent tab labels
    'tab-main': '主控',
    'tab-bookmarker': '书签员',
    'tab-trader': '交易员',
    'tab-claude-dispatch': 'Claude Dispatch（鲁班）',
    // AutoResearch
    'ar-experiment': '实验',
    'ar-execute': '执行',
    'ar-evaluate': '评估',
    'ar-decide': '保留/丢弃',
    'ar-next': '下一轮',
    'ar-recent-verdicts': '最近判定',
    'ar-strategy-experiment': '策略实验',
    // Operator lanes
    'lane-observe': '观察',
    'lane-decide': '决策',
    'lane-execute': '执行',
    // Agent Operating Card labels
    'aoc-role': '角色',
    'aoc-mode': '模式',
    'aoc-freshness': '数据时效',
    'aoc-blocker': '阻塞点',
    'aoc-next-action': '下一步动作',
    // Common labels
    'label-last-active': '最后活跃',
    'label-mode': '模式',
    'label-risk': '风险',
    'label-status': '状态',
    'label-completed-at': '完成时间',
    // Main tab
    'section-social-intent': '社交意图',
    'section-treasury': '国库策略',
    'section-last-decision': '最新决策',
    'section-social-pipeline': '社交执行链路',
    'section-main-social-control-plane': 'Main 社交控制平面',
    'section-social-actions': '策展动作历史',
    'section-budget': '预算分配',
    // Bookmarker tab
    'section-topic-brief': '话题摘要',
    'section-content-candidates': '内容候选',
    'section-social-drafts': '社交草稿',
    'section-source-health': '数据源健康',
    'section-align-events': '对齐事件',
    // Trader tab
    'section-wallet': '钱包余额',
    'section-rewards': '可领奖励',
    'section-risk-flags': '风险标志',
    'section-risk-detail': '风险标志',
    'section-trade-actions': '交易动作',
    'section-onchain': '链上持仓',
    'section-community-heat': '社区热度',
    // Claude Dispatch tab
    'cd-task-summary': '任务摘要',
    'cd-files-changed': '变更文件',
    'cd-test-results': '测试结果',
    'cd-tests-passed': '测试通过',
    'cd-pass-count': '通过数',
    'cd-fail-count': '失败数',
    'cd-dispatch-roi': '调度 ROI',
    'cd-built-tools': '已开发工具',
    'cd-result-links': '结果链接',
    'cd-blockers': '阻塞项',
    'cd-no-task': '无活跃任务 — 等待 main 派单',
    'cd-idle': '空闲',
    'cd-current-task': '当前任务',
    'cd-latest-result': '最近完成',
    'cd-roi-mismatch': 'ROI 来自其他任务',
    'cd-stage-mismatch': 'Stage 来自其他任务',
    'cd-task-id': '任务 ID',
    // Dev panel (legacy, kept for backward compat)
    'section-result-summary': '结果摘要',
    'section-result-links': '结果链接',
    // Mission Status Bar (slots removed)
    // NOC / Intelligence
    'noc-dep-graph': '🔗 流水线依赖图',
    'noc-state-machines': '⚙ 执行状态机',
    'noc-countdown': '⏱ 倒计时面板',
    'noc-community-heat': '🔥 社区热度',
    'noc-intel-summary': '📊 情报概览',
    // Timeline
    'timeline-filter-note': '仅显示已完成动作',
    'tl-posts': '帖子',
    'tl-curations': '策展',
    'tl-claims': '领取',
    'tl-blocked': '阻塞',
    'tl-dominant': '主导智能体',
    'tl-last-ok': '最近成功',
    // Common status / value strings
    'no-data': '无数据',
    'no-actions': '无动作',
    'no-timeline': '无时间轴数据',
    'fetch-error': '获取错误：',
    'no-social-actions': '暂无动作记录',
    'no-trade-actions': '暂无交易记录',
    'no-tas-history': '暂无 TAS 历史',
    'tas-accumulating': '数据积累中',
    'no-balance': '无余额数据',
    'no-rewards': '无可领奖励',
    'no-risk-flags': '无风险标记',
    'total-value': '总价值',
    'portfolio-share': '组合占比',
    'no-dev-result': '暂无鲁班交付结果',
    'no-links': '暂无链接',
    'live-dashboard': '在线仪表板',
    'github-repo': 'GitHub 仓库',
    'decision-social': '社交',
    'decision-treasury': '金库',
    'candidates-unit': '个候选',
    'no-candidates': '无候选',
    'shared-executor': '共享执行器 — Main 也通过此路径发布',
    'no-blockers': '无活跃阻塞',
    // Freshness matrix labels
    'fm-fresh': '新鲜',
    'fm-aging': '老化',
    'fm-stale': '陈旧',
    'fm-critical': '严重',
    'fm-na': '不适用',
    // P1/P2 additions
    'npp-title': '下一条帖子（待发布）',
    // P10: Explainability
    'section-explainability': '🔍 制品可解释性',
    'explain-artifact-state': '制品状态与溯源',
    'explain-health-ctx': '健康上下文',
    'explain-recent-events': '近期 Wiki 事件',
  },
  en: {
    subtitle: 'Agent Dashboard',
    refresh: '↻ Refresh',
    'section-tas-command': '🎯 TAS Command Center',
    'col-tas-social': 'TAS_social',
    'col-tas-aggregate': 'Aggregated TAS',
    'col-tas-trade': 'TAS_trade',
    'section-autoresearch': '🔬 AutoResearch Evolution Loop',
    'section-agent-details': '🤖 Agent Details',
    'section-wiki-intel': '📚 self-IP LLM Wiki — Intelligence Layer',
    'section-timeline': '⏱ Action Timeline',
    'section-operator-lanes': '🔄 Operator Lanes',
    'panel-dev': '🔨 Claude Dispatch (Luban)',
    'tab-main': 'Main',
    'tab-bookmarker': 'Bookmarker',
    'tab-trader': 'Trader',
    'tab-claude-dispatch': 'Claude Dispatch',
    'ar-experiment': 'Experiment',
    'ar-execute': 'Execute',
    'ar-evaluate': 'Evaluate',
    'ar-decide': 'Keep/Discard',
    'ar-next': 'Next Round',
    'ar-recent-verdicts': 'Recent Verdicts',
    'ar-strategy-experiment': 'Strategy Experiment',
    'lane-observe': 'OBSERVE',
    'lane-decide': 'DECIDE',
    'lane-execute': 'EXECUTE',
    'aoc-role': 'Role',
    'aoc-mode': 'Mode',
    'aoc-freshness': 'Freshness',
    'aoc-blocker': 'Blocker',
    'aoc-next-action': 'Next Action',
    'label-last-active': 'last active',
    'label-mode': 'Mode',
    'label-risk': 'Risk',
    'label-status': 'Status',
    'label-completed-at': 'Completed',
    'section-social-intent': 'Social Intent',
    'section-treasury': 'Treasury Policy',
    'section-last-decision': 'Last Decision',
    'section-social-pipeline': 'Social Execution Pipeline',
    'section-main-social-control-plane': 'Main Social Control Plane',
    'section-social-actions': 'Social Actions',
    'section-budget': 'Budget Allocation',
    'section-topic-brief': 'Topic Brief',
    'section-content-candidates': 'Content Candidates',
    'section-social-drafts': 'Social Drafts',
    'section-source-health': 'Source Health',
    'section-align-events': 'Align Events',
    'section-wallet': 'Wallet Balances',
    'section-rewards': 'Claimable Rewards',
    'section-risk-flags': 'Risk Flags',
    'section-risk-detail': 'Risk Flags',
    'section-trade-actions': 'Trade Actions',
    'section-onchain': 'Onchain Positions',
    'section-community-heat': 'Community Heat',
    'cd-task-summary': 'Task Summary',
    'cd-files-changed': 'Files Changed',
    'cd-test-results': 'Test Results',
    'cd-tests-passed': 'Tests Passed',
    'cd-pass-count': 'Pass',
    'cd-fail-count': 'Fail',
    'cd-dispatch-roi': 'Dispatch ROI',
    'cd-built-tools': 'Built Tools',
    'cd-result-links': 'Result Links',
    'cd-blockers': 'Blockers',
    'cd-no-task': 'No active task — awaiting dispatch from main',
    'cd-idle': 'idle',
    'cd-current-task': 'Current Task',
    'cd-latest-result': 'Latest Completed',
    'cd-roi-mismatch': 'ROI from different task',
    'cd-stage-mismatch': 'Stage from different task',
    'cd-task-id': 'Task ID',
    'section-result-summary': 'Result Summary',
    'section-result-links': 'Result Links',
    // Mission Status Bar (slots removed)
    'noc-dep-graph': '🔗 Pipeline Dependency Graph',
    'noc-state-machines': '⚙ Execution State Machines',
    'noc-countdown': '⏱ Countdown Strip',
    'noc-community-heat': '🔥 Community Heat',
    'noc-intel-summary': '📊 Intelligence Summary',
    'timeline-filter-note': 'Completed actions only',
    'tl-posts': 'Posts',
    'tl-curations': 'Curations',
    'tl-claims': 'Claims',
    'tl-blocked': 'Blocked',
    'tl-dominant': 'Dominant',
    'tl-last-ok': 'Last OK',
    'no-data': 'No data',
    'no-actions': 'No actions',
    'no-timeline': 'No timeline data',
    'fetch-error': 'Fetch error: ',
    'no-social-actions': 'No actions yet',
    'no-trade-actions': 'No trade actions',
    'no-tas-history': 'No TAS history',
    'tas-accumulating': 'Accumulating data',
    'no-balance': 'No balance data',
    'no-rewards': 'No claimable rewards',
    'no-risk-flags': 'No risk flags',
    'total-value': 'Total Value',
    'portfolio-share': 'Portfolio share',
    'no-dev-result': 'No deliverable yet',
    'no-links': 'No links',
    'live-dashboard': 'Live Dashboard',
    'github-repo': 'GitHub Repository',
    'decision-social': 'Social',
    'decision-treasury': 'Treasury',
    'candidates-unit': 'candidate(s)',
    'no-candidates': 'No candidates',
    'shared-executor': 'Shared executor — Main also publishes via this path',
    'no-blockers': 'No active blockers',
    'fm-fresh': 'fresh',
    'fm-aging': 'aging',
    'fm-stale': 'stale',
    'fm-critical': 'critical',
    'fm-na': 'n/a',
    // P1/P2 additions
    'npp-title': 'Next Post (Pending)',
    // P10: Explainability
    'section-explainability': '🔍 Artifact Explainability',
    'explain-artifact-state': 'Artifact State & Provenance',
    'explain-health-ctx': 'Health Context',
    'explain-recent-events': 'Recent Wiki Events',
  },
};

let _lang = localStorage.getItem('tcx_lang') || 'zh';
let _devMode = true;
let _lastStatus = null;
let _lastTimeline = null;
let _lastAutoResearch = null;
let _lastControlTower = null;
let _lastAgentHealth = null;
let _lastNoc = null;
let _lastExplainability = null;

function t(key) {
  return (I18N[_lang] || I18N.zh)[key] ?? (I18N.zh)[key] ?? key;
}

function isZh() {
  return _lang === 'zh';
}

function langText(zh, en) {
  return isZh() ? zh : en;
}

const TEXT_NODE_I18N = {
  'Agent Dashboard': { zh: '智能体看板', en: 'Agent Dashboard' },
  '智能体看板': { zh: '智能体看板', en: 'Agent Dashboard' },
  'EN': { zh: '英文', en: 'EN' },
  '英文': { zh: '英文', en: 'EN' },
  '中文': { zh: '中文', en: 'Chinese' },
  'Chinese': { zh: '中文', en: 'Chinese' },
  'main': { zh: '主控', en: 'main' },
  '主控': { zh: '主控', en: 'main' },
  'bookmarker': { zh: '书签员', en: 'bookmarker' },
  '书签员': { zh: '书签员', en: 'bookmarker' },
  'trader': { zh: '交易员', en: 'trader' },
  '交易员': { zh: '交易员', en: 'trader' },
  'No active blockers': { zh: '无活跃阻塞', en: 'No active blockers' },
  '无活跃阻塞': { zh: '无活跃阻塞', en: 'No active blockers' },
  'cycle_count': { zh: '周期数', en: 'cycle_count' },
  '周期数': { zh: '周期数', en: 'cycle_count' },
  'align_score': { zh: '对齐得分', en: 'align_score' },
  '对齐得分': { zh: '对齐得分', en: 'align_score' },
  'community_score': { zh: '社区得分', en: 'community_score' },
  '社区得分': { zh: '社区得分', en: 'community_score' },
  'pob_reward_score': { zh: 'PoB 奖励得分', en: 'pob_reward_score' },
  'PoB 奖励得分': { zh: 'PoB 奖励得分', en: 'pob_reward_score' },
  'Track B source': { zh: 'B 轨来源', en: 'Track B source' },
  'B 轨来源': { zh: 'B 轨来源', en: 'Track B source' },
  'portfolio_usd': { zh: '持仓美元值', en: 'portfolio_usd' },
  '持仓美元值': { zh: '持仓美元值', en: 'portfolio_usd' },
  'portfolio_norm': { zh: '持仓归一化', en: 'portfolio_norm' },
  '持仓归一化': { zh: '持仓归一化', en: 'portfolio_norm' },
  'claimable_usd': { zh: '可领奖励美元值', en: 'claimable_usd' },
  '可领奖励美元值': { zh: '可领奖励美元值', en: 'claimable_usd' },
  'claimable_norm': { zh: '可领奖励归一化', en: 'claimable_norm' },
  '可领奖励归一化': { zh: '可领奖励归一化', en: 'claimable_norm' },
  'pob_reward': { zh: 'PoB 奖励', en: 'pob_reward' },
  'PoB 奖励': { zh: 'PoB 奖励', en: 'pob_reward' },
  'Evolution Cycle': { zh: '演化周期', en: 'Evolution Cycle' },
  '演化周期': { zh: '演化周期', en: 'Evolution Cycle' },
  '(heartbeat)': { zh: '（heartbeat）', en: '(heartbeat)' },
  '（heartbeat）': { zh: '（heartbeat）', en: '(heartbeat)' },
  '(arm strategy)': { zh: '（挂载策略）', en: '(arm strategy)' },
  '（挂载策略）': { zh: '（挂载策略）', en: '(arm strategy)' },
  '(TAS delta)': { zh: '（TAS 变化）', en: '(TAS delta)' },
  '（TAS 变化）': { zh: '（TAS 变化）', en: '(TAS delta)' },
  '(verdict)': { zh: '（判定）', en: '(verdict)' },
  '（判定）': { zh: '（判定）', en: '(verdict)' },
  '(cycle++)': { zh: '（周期++）', en: '(cycle++)' },
  '（周期++）': { zh: '（周期++）', en: '(cycle++)' },
  'Track A (trader)': { zh: 'A 轨（交易员）', en: 'Track A (trader)' },
  'A 轨（交易员）': { zh: 'A 轨（交易员）', en: 'Track A (trader)' },
  'Track B (bookmarker)': { zh: 'B 轨（书签员）', en: 'Track B (bookmarker)' },
  'B 轨（书签员）': { zh: 'B 轨（书签员）', en: 'Track B (bookmarker)' },
  'coupling_alpha': { zh: '耦合系数', en: 'coupling_alpha' },
  '耦合系数': { zh: '耦合系数', en: 'coupling_alpha' },
  'Wallet Snapshot': { zh: '钱包快照', en: 'Wallet Snapshot' },
  '钱包快照': { zh: '钱包快照', en: 'Wallet Snapshot' },
  'Reward Status': { zh: '奖励状态', en: 'Reward Status' },
  '奖励状态': { zh: '奖励状态', en: 'Reward Status' },
  'Risk Status': { zh: '风险状态', en: 'Risk Status' },
  '风险状态': { zh: '风险状态', en: 'Risk Status' },
  'Mode': { zh: '模式', en: 'Mode' },
  '模式': { zh: '模式', en: 'Mode' },
  'Risk': { zh: '风险', en: 'Risk' },
  '风险': { zh: '风险', en: 'Risk' },
  'coupling_active': { zh: '耦合激活', en: 'coupling_active' },
  '耦合激活': { zh: '耦合激活', en: 'coupling_active' },
  'PoB Unclaimed': { zh: '未领取 PoB', en: 'PoB Unclaimed' },
  '未领取 PoB': { zh: '未领取 PoB', en: 'PoB Unclaimed' },
  '$2.00 claim threshold': { zh: '$2.00 领取阈值', en: '$2.00 claim threshold' },
  '$2.00 领取阈值': { zh: '$2.00 领取阈值', en: '$2.00 claim threshold' },
  'Data Layer Overview': { zh: '数据层总览', en: 'Data Layer Overview' },
  '数据层总览': { zh: '数据层总览', en: 'Data Layer Overview' },
  'Raw(read-only) → LLM Compile → Wiki(compiled) → Agent Heartbeat Read(decisions)': { zh: 'Raw（只读）→ LLM 编译 → Wiki（已整理）→ Agent 心跳读取（决策）', en: 'Raw(read-only) → LLM Compile → Wiki(compiled) → Agent Heartbeat Read(decisions)' },
  'Raw（只读）→ LLM 编译 → Wiki（已整理）→ Agent 心跳读取（决策）': { zh: 'Raw（只读）→ LLM 编译 → Wiki（已整理）→ Agent 心跳读取（决策）', en: 'Raw(read-only) → LLM Compile → Wiki(compiled) → Agent Heartbeat Read(decisions)' },
  'Raw': { zh: '原始层', en: 'Raw' },
  '原始层': { zh: '原始层', en: 'Raw' },
  'Execution Brief': { zh: '执行摘要', en: 'Execution Brief' },
  '执行摘要': { zh: '执行摘要', en: 'Execution Brief' },
  'Ingest Pipeline Matrix': { zh: '摄取流水线矩阵', en: 'Ingest Pipeline Matrix' },
  '摄取流水线矩阵': { zh: '摄取流水线矩阵', en: 'Ingest Pipeline Matrix' },
  'Pipeline': { zh: '流水线', en: 'Pipeline' },
  '流水线': { zh: '流水线', en: 'Pipeline' },
  'Script': { zh: '脚本', en: 'Script' },
  '脚本': { zh: '脚本', en: 'Script' },
  'Freq': { zh: '频率', en: 'Freq' },
  '频率': { zh: '频率', en: 'Freq' },
  'Raw → Wiki': { zh: '原始层 → Wiki', en: 'Raw → Wiki' },
  '原始层 → Wiki': { zh: '原始层 → Wiki', en: 'Raw → Wiki' },
  'Last Run': { zh: '最近运行', en: 'Last Run' },
  '最近运行': { zh: '最近运行', en: 'Last Run' },
  'Agent Wiki-first Status': { zh: '智能体 Wiki-first 状态', en: 'Agent Wiki-first Status' },
  '智能体 Wiki-first 状态': { zh: '智能体 Wiki-first 状态', en: 'Agent Wiki-first Status' },
  'Community Heat Map (legacy)': { zh: '社区热度图（兼容）', en: 'Community Heat Map (legacy)' },
  '社区热度图（兼容）': { zh: '社区热度图（兼容）', en: 'Community Heat Map (legacy)' },
  'Loading…': { zh: '加载中…', en: 'Loading…' },
  '加载中…': { zh: '加载中…', en: 'Loading…' },
  'Pass': { zh: '通过', en: 'Pass' },
  '通过': { zh: '通过', en: 'Pass' },
  'Fail': { zh: '失败', en: 'Fail' },
  '失败': { zh: '失败', en: 'Fail' },
  'Stage': { zh: '阶段', en: 'Stage' },
  '阶段': { zh: '阶段', en: 'Stage' },
  'Completed': { zh: '完成时间', en: 'Completed' },
  '完成时间': { zh: '完成时间', en: 'Completed' },
  'Dispatch ROI': { zh: '调度 ROI', en: 'Dispatch ROI' },
  '调度 ROI': { zh: '调度 ROI', en: 'Dispatch ROI' },
  'Task Summary': { zh: '任务摘要', en: 'Task Summary' },
  '任务摘要': { zh: '任务摘要', en: 'Task Summary' },
  'Files Changed': { zh: '变更文件', en: 'Files Changed' },
  '变更文件': { zh: '变更文件', en: 'Files Changed' },
  'Test Results': { zh: '测试结果', en: 'Test Results' },
  '测试结果': { zh: '测试结果', en: 'Test Results' },
  'Built Tools': { zh: '已开发工具', en: 'Built Tools' },
  '已开发工具': { zh: '已开发工具', en: 'Built Tools' },
  'Result Links': { zh: '结果链接', en: 'Result Links' },
  '结果链接': { zh: '结果链接', en: 'Result Links' },
  'Action Timeline': { zh: '行动时间轴', en: 'Action Timeline' },
  '行动时间轴': { zh: '行动时间轴', en: 'Action Timeline' },
  'Completed actions only': { zh: '仅显示已完成动作', en: 'Completed actions only' },
  '仅显示已完成动作': { zh: '仅显示已完成动作', en: 'Completed actions only' },
  '🔗 Pipeline Dependency Graph': { zh: '🔗 流水线依赖图', en: '🔗 Pipeline Dependency Graph' },
  '🔗 流水线依赖图': { zh: '🔗 流水线依赖图', en: '🔗 Pipeline Dependency Graph' },
  '⚙ Execution State Machines': { zh: '⚙ 执行状态机', en: '⚙ Execution State Machines' },
  '⚙ 执行状态机': { zh: '⚙ 执行状态机', en: '⚙ Execution State Machines' },
  '⏱ Countdown Strip': { zh: '⏱ 倒计时面板', en: '⏱ Countdown Strip' },
  '⏱ 倒计时面板': { zh: '⏱ 倒计时面板', en: '⏱ Countdown Strip' },
  '📊 Intelligence Summary': { zh: '📊 情报概览', en: '📊 Intelligence Summary' },
  '📊 情报概览': { zh: '📊 情报概览', en: '📊 Intelligence Summary' },
  '📚 self-IP LLM Wiki — Intelligence Layer': { zh: '📚 self-IP LLM Wiki — 知识层', en: '📚 self-IP LLM Wiki — Intelligence Layer' },
  '📚 self-IP LLM Wiki — 知识层': { zh: '📚 self-IP LLM Wiki — 知识层', en: '📚 self-IP LLM Wiki — Intelligence Layer' },
  'Next Post (Pending)': { zh: '下一条帖子（待发布）', en: 'Next Post (Pending)' },
  '下一条帖子（待发布）': { zh: '下一条帖子（待发布）', en: 'Next Post (Pending)' },
  '展开': { zh: '展开', en: 'Expand' },
  'Expand': { zh: '展开', en: 'Expand' },
  '收起': { zh: '收起', en: 'Collapse' },
  'Collapse': { zh: '收起', en: 'Collapse' },
  'A轨': { zh: 'A轨', en: 'Track A' },
  'Track A': { zh: 'A轨', en: 'Track A' },
  'B轨': { zh: 'B轨', en: 'Track B' },
  'Track B': { zh: 'B轨', en: 'Track B' },
  'no data': { zh: '无数据', en: 'no data' },
  '无数据': { zh: '无数据', en: 'no data' },
  'no themes': { zh: '无主题', en: 'no themes' },
  '无主题': { zh: '无主题', en: 'no themes' },
  'no wiki fields': { zh: '无 Wiki 字段', en: 'no wiki fields' },
  '无 Wiki 字段': { zh: '无 Wiki 字段', en: 'no wiki fields' },
  'source': { zh: '来源', en: 'source' },
  'window': { zh: '窗口', en: 'window' },
  'normalization': { zh: '归一化', en: 'normalization' },
  'formula': { zh: '公式', en: 'formula' },
  'baseline': { zh: '基准', en: 'baseline' },
  'confidence': { zh: '置信度', en: 'confidence' },
  'scorer': { zh: '评分器', en: 'scorer' },
  'control plane': { zh: '控制平面', en: 'control plane' },
  'mode': { zh: '模式', en: 'mode' },
  'coupling': { zh: '耦合', en: 'coupling' },
  'breaker:': { zh: '熔断器：', en: 'breaker:' },
  'closed': { zh: '关闭', en: 'closed' },
  'skipped': { zh: '跳过', en: 'skipped' },
  'no': { zh: '否', en: 'no' },
  'post': { zh: '发帖', en: 'post' },
  'claim': { zh: '领取', en: 'claim' },
  'Main Agent': { zh: '主控 Agent', en: 'Main Agent' },
  'X Sync': { zh: 'X 同步', en: 'X Sync' },
  'Topic Brief': { zh: '话题摘要', en: 'Topic Brief' },
  'Content Candidates': { zh: '内容候选', en: 'Content Candidates' },
  'Social Drafts': { zh: '社交草稿', en: 'Social Drafts' },
  'Autonomy Intent': { zh: '自主意图', en: 'Autonomy Intent' },
  'Execution': { zh: '执行', en: 'Execution' },
  'Source Health': { zh: '数据源健康', en: 'Source Health' },
  'Reinforce Strategy': { zh: '强化当前策略', en: 'Reinforce Strategy' },
  'PoB Reward Track': { zh: 'PoB 奖励轨', en: 'PoB Reward Track' },
  'Portfolio Normalization': { zh: '持仓归一化', en: 'Portfolio Normalization' },
  'Claimable Normalization': { zh: '可领奖励归一化', en: 'Claimable Normalization' },
  'Credit & Strategy Detail': { zh: 'Credit 与策略细节', en: 'Credit & Strategy Detail' },
  'maximize TAS_social': { zh: '最大化 TAS_social', en: 'maximize TAS_social' },
  'maximize TAS_trade': { zh: '最大化 TAS_trade', en: 'maximize TAS_trade' },
  'stable': { zh: '稳定', en: 'stable' },
  'Target metric': { zh: '目标指标', en: 'Target metric' },
  'unmeasured': { zh: '未测量', en: 'unmeasured' },
  'social': { zh: '社交', en: 'social' },
  'curate': { zh: '策展', en: 'curate' },
  'decision': { zh: '决策', en: 'decision' },
  'heartbeat': { zh: '心跳', en: 'heartbeat' },
  'Social Pipeline': { zh: '社交流水线', en: 'Social Pipeline' },
  'Social Intent': { zh: '社交意图', en: 'Social Intent' },
  'Social Actions': { zh: '社交动作', en: 'Social Actions' },
  'Treasury Pipeline': { zh: '国库流水线', en: 'Treasury Pipeline' },
  'Treasury Policy': { zh: '国库策略', en: 'Treasury Policy' },
  'Claim/Trade': { zh: '领取/交易', en: 'Claim/Trade' },
  'Wiki Pipeline': { zh: 'Wiki 流水线', en: 'Wiki Pipeline' },
  'Wiki Compile': { zh: 'Wiki 编译', en: 'Wiki Compile' },
  'Agent Read': { zh: 'Agent 读取', en: 'Agent Read' },
  'Decision': { zh: '决策', en: 'Decision' },
  'observe': { zh: '观察', en: 'observe' },
  'plan': { zh: '规划', en: 'plan' },
  'write_intents': { zh: '写入意图', en: 'write_intents' },
  'verify': { zh: '校验', en: 'verify' },
  'sync': { zh: '同步', en: 'sync' },
  'brief': { zh: '摘要', en: 'brief' },
  'draft': { zh: '草稿', en: 'draft' },
  'execute': { zh: '执行', en: 'execute' },
  'publish': { zh: '发布', en: 'publish' },
  'score': { zh: '评分', en: 'score' },
  'X sync': { zh: 'X 同步', en: 'X sync' },
  'Align Track — @0xNought interactions': { zh: '对齐轨 — @0xNought 互动', en: 'Align Track — @0xNought interactions' },
  'Community Track — all users': { zh: '社区轨 — 全体用户', en: 'Community Track — all users' },
  'weight in TAS_trade': { zh: 'TAS_trade 中权重', en: 'weight in TAS_trade' },
  'holding_trend': { zh: '持仓趋势', en: 'holding_trend' },
  'measurement_quality': { zh: '测量质量', en: 'measurement_quality' },
  'recommended_actions': { zh: '建议动作', en: 'recommended_actions' },
  'claim_history_score': { zh: '领取历史得分', en: 'claim_history_score' },
  'credit_rank_score': { zh: 'Credit 排名得分', en: 'credit_rank_score' },
  'credit_strategy': { zh: 'Credit 策略', en: 'credit_strategy' },
  'holding_trend_score': { zh: '持仓趋势得分', en: 'holding_trend_score' },
  'stake_eligible': { zh: '可 Stake', en: 'stake_eligible' },
  'lp_eligible': { zh: '可 LP', en: 'lp_eligible' },
  'strategy_action': { zh: '策略动作', en: 'strategy_action' },
  'execution_allowed': { zh: '允许执行', en: 'execution_allowed' },
  'align_event_active': { zh: '对齐事件激活', en: 'align_event_active' },
  'has_active_event': { zh: '存在活跃事件', en: 'has_active_event' },
  'align_count_24h': { zh: '24h 对齐次数', en: 'align_count_24h' },
  'total_likes': { zh: '总点赞', en: 'total_likes' },
  'total_retweets': { zh: '总转发', en: 'total_retweets' },
  'total_replies': { zh: '总回复', en: 'total_replies' },
  'total_interactions': { zh: '总互动', en: 'total_interactions' },
  'Main HB': { zh: '主控心跳', en: 'Main HB' },
  'Bookmarker HB': { zh: '书签员心跳', en: 'Bookmarker HB' },
  'Trader HB': { zh: '交易员心跳', en: 'Trader HB' },
  'Intent Expiry': { zh: '意图过期', en: 'Intent Expiry' },
  'Treasury Expiry': { zh: '国库过期', en: 'Treasury Expiry' },
  'Wiki Brief Valid': { zh: 'Wiki 摘要有效期', en: 'Wiki Brief Valid' },
  'Claim Progress': { zh: '领取进度', en: 'Claim Progress' },
  'Top Themes': { zh: '热门主题', en: 'Top Themes' },
  'Stale Paths': { zh: '陈旧路径', en: 'Stale Paths' },
  'all fresh': { zh: '全部新鲜', en: 'all fresh' },
  'Hottest': { zh: '最热信号', en: 'Hottest' },
  'no signal': { zh: '无信号', en: 'no signal' },
  'na': { zh: '不适用', en: 'na' },
  'TAS trend declining': { zh: 'TAS 趋势下滑', en: 'TAS trend declining' },
  'TAS declined': { zh: 'TAS 已下降', en: 'TAS declined' },
  'Switch Strategy': { zh: '切换策略', en: 'Switch Strategy' },
  'decide': { zh: '决策', en: 'decide' },
  'exec_cycle': { zh: '执行周期', en: 'exec_cycle' },
  'est.': { zh: '预计', en: 'est.' },
  'compiled_at': { zh: '编译时间', en: 'compiled_at' },
  'valid_until': { zh: '有效期至', en: 'valid_until' },
  'tokens': { zh: '代币', en: 'tokens' },
  'Status': { zh: '状态', en: 'Status' },
  'Platform Snapshot': { zh: '平台快照', en: 'Platform Snapshot' },
  'Docs Ingest': { zh: '文档摄取', en: 'Docs Ingest' },
  'Topic Heatmap': { zh: '话题热力图', en: 'Topic Heatmap' },
  'Social Snapshot': { zh: '社交快照', en: 'Social Snapshot' },
  'bookmark-sync cron': { zh: 'bookmark-sync 定时任务', en: 'bookmark-sync cron' },
  'bookmarker heartbeat': { zh: 'bookmarker 心跳', en: 'bookmarker heartbeat' },
  'monthly': { zh: '每月', en: 'monthly' },
  'weekly': { zh: '每周', en: 'weekly' },
  'raw_align': { zh: '原始对齐分', en: 'raw_align' },
  'pob_claimable_usd': { zh: 'PoB 可领奖励美元值', en: 'pob_claimable_usd' },
  'baseline_usd': { zh: '基准美元值', en: 'baseline_usd' },
  'curate_reward_usd': { zh: '策展奖励美元值', en: 'curate_reward_usd' },
  'creator_reward_usd': { zh: '创作奖励美元值', en: 'creator_reward_usd' },
  'portfolio_usd_raw': { zh: '原始持仓美元值', en: 'portfolio_usd_raw' },
  'portfolio_value_score': { zh: '持仓价值得分', en: 'portfolio_value_score' },
  'claimable_usd_raw': { zh: '原始可领奖励美元值', en: 'claimable_usd_raw' },
  'add_lp': { zh: '增加 LP', en: 'add_lp' },
  'intent ✓': { zh: '意图 ✓', en: 'intent ✓' },
  'main_guidance': { zh: '主控引导', en: 'main_guidance' },
  'post_new': { zh: '发新帖', en: 'post_new' },
  'balanced': { zh: '平衡', en: 'balanced' },
  'explore': { zh: '探索', en: 'explore' },
  'tas_trade_partial': { zh: 'TAS_trade 部分完成', en: 'tas_trade_partial' },
  'high_signal: urgency:': { zh: '高信号：紧迫度：', en: 'high_signal: urgency:' },
  'Wiki Lint': { zh: 'Wiki 检查', en: 'Wiki Lint' },
  'Query Writeback': { zh: '查询回写', en: 'Query Writeback' },
  'Community Heat': { zh: '社区热度', en: 'Community Heat' },
  'per heartbeat': { zh: '每次 heartbeat', en: 'per heartbeat' },
  'brief_available': { zh: '摘要可用', en: 'brief_available' },
  'top_theme': { zh: '顶部主题', en: 'top_theme' },
  'content_direction': { zh: '内容方向', en: 'content_direction' },
  'trending_ticks': { zh: '热门 ticks', en: 'trending_ticks' },
  'platform_available': { zh: '平台可用', en: 'platform_available' },
  'credit_vp_threshold': { zh: 'Credit/VP 阈值', en: 'credit_vp_threshold' },
  'IN_REVIEW': { zh: '审核中', en: 'IN_REVIEW' },
  'Agent-Infrastructure': { zh: 'Agent 基础设施', en: 'Agent-Infrastructure' },
  'AgentInfrastructure': { zh: 'Agent 基础设施', en: 'AgentInfrastructure' },
  'Token-Economy': { zh: 'Token 经济', en: 'Token-Economy' },
  'AgentSwarm': { zh: 'Agent 群体', en: 'AgentSwarm' },
  'Lint：': { zh: '检查：', en: 'Lint:' },
  'Projects': { zh: '项目', en: 'Projects' },
  'bookmark · post': { zh: '收藏 · 发帖', en: 'bookmark · post' },
  'conservative_explore': { zh: '保守探索', en: 'conservative_explore' },
  'Conservative Explore': { zh: '保守探索', en: 'Conservative Explore' },
  '→ post, curate': { zh: '→ 发帖，策展', en: '→ post, curate' },
  '→ stable': { zh: '→ 稳定', en: '→ stable' },
  'tas': { zh: 'TAS', en: 'tas' },
  'intent': { zh: '意图', en: 'intent' },
  'pipeline': { zh: '流水线', en: 'pipeline' },
  'wallet': { zh: '钱包', en: 'wallet' },
  'wiki': { zh: 'Wiki', en: 'wiki' },
  'skills': { zh: '技能', en: 'skills' },
  '🧠 main': { zh: '🧠 主控', en: '🧠 main' },
  '📌 bookmarker': { zh: '📌 书签员', en: '📌 bookmarker' },
  '💰 trader': { zh: '💰 交易员', en: '💰 trader' },
  '📋 Wiki Data Layer Details': { zh: '📋 Wiki 数据层细节', en: '📋 Wiki Data Layer Details' },
};

const INVARIANT_TICKS = new Set(['TagClaw', 'BUIDL', 'TTAI', 'CLAW', 'AGENT', 'NOUGHT']);

function isInvariantTickLiteral(s) {
  if (!s) return false;
  if (INVARIANT_TICKS.has(s)) return true;
  if ([...INVARIANT_TICKS].some(t => s === `${t} →`)) return true;
  if ([...INVARIANT_TICKS].some(t => s.startsWith(`${t} $`))) return true;
  return false;
}

function isCanonicalPathLike(s) {
  if (!s) return false;
  if (/^https?:\/\//.test(s)) return true;
  if (/^[\w./-]+\.(py|sh|md|json|ts|js)$/i.test(s)) return true;
  if (/^(raw|wiki|runtime|scripts)\//.test(s)) return true;
  if (/^→\s+[\w./-]+/.test(s)) return true;
  if (s.includes('/')) return true;
  return false;
}

function isFormulaLike(s) {
  if (!s) return false;
  if (/norm\(/i.test(s)) return true;
  if (/capped at/i.test(s)) return true;
  if (/^raw\s*\/\s*\d/.test(s)) return true;
  if (/^interactions\s*\/\s*\d/.test(s)) return true;
  if (/^0\.\d+×/.test(s)) return true;
  return false;
}

function isCanonicalKeyLike(s) {
  if (!s) return false;
  if (/^@/.test(s)) return true;
  if (/^[a-z0-9_]+$/.test(s) && s.includes('_')) return true;
  if (/^[a-z0-9-]+$/.test(s) && s.includes('-')) return true;
  if (/^[A-Za-z0-9-]+\s+\d+\.\d+$/.test(s)) return true;
  return false;
}

function shouldPreserveCanonicalLiteral(s) {
  if (!s) return false;
  if (isInvariantTickLiteral(s)) return true;
  if (isCanonicalPathLike(s)) return true;
  if (isFormulaLike(s)) return true;
  if (isCanonicalKeyLike(s)) return true;
  return false;
}

function translateLiteral(value, lang) {
  if (value === null || value === undefined) return value;
  const s = String(value);
  const l = lang || _lang || 'zh';
  const direct = TEXT_NODE_I18N[s];
  if (direct) return direct[l] || direct.en || direct.zh || s;

  if (l === 'zh') {
    let m;
    if ((m = s.match(/^(\d+) candidates?$/))) return `${m[1]} 个候选`;
    if ((m = s.match(/^(\d+) drafts?$/))) return `${m[1]} 条草稿`;
    if ((m = s.match(/^(\d+\/\d+) ok$/))) return `${m[1]} 正常`;
    if ((m = s.match(/^Recent posts \((\d+)\)$/))) return `最近帖子（${m[1]}）`;
    if ((m = s.match(/^(\d+) @clawdbot posts$/))) return `${m[1]} 条 @clawdbot 帖子`;
    if ((m = s.match(/^source: (.+)$/))) return `来源：${m[1]}`;
    if ((m = s.match(/^mode: (.+)$/))) return `模式：${translateLiteral(m[1], l)}`;
    if ((m = s.match(/^→ (.+)$/)) && !isCanonicalPathLike(s)) return `→ ${m[1].split(/,\s*/).map(x => translateLiteral(x, l)).join('，')}`;
    if ((m = s.match(/^(.+) ago$/))) return `${translateLiteral(m[1], l)}前`;
    if ((m = s.match(/^(.+) rolling$/))) return `${translateLiteral(m[1], l)} 滚动窗口`;
    if ((m = s.match(/^(\d+)h$/))) return `${m[1]}小时`;
    if ((m = s.match(/^(\d+)m$/))) return `${m[1]}分钟`;
    if ((m = s.match(/^(\d+)d$/))) return `${m[1]}天`;
    if ((m = s.match(/^post-(.+)$/))) return `发帖-${m[1]}`;
    if ((m = s.match(/^main_guidance: (.+)$/))) return `主控引导：${m[1].split(/,\s*/).map(x => translateLiteral(x, l)).join('，')}`;
  }

  if (shouldPreserveCanonicalLiteral(s)) return s;

  const human = (typeof humanize === 'function') ? humanize(s, l) : s;
  return human;
}

function translateDomTextNodes(root = document.body) {
  if (!root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node || !node.parentNode) return NodeFilter.FILTER_REJECT;
      const parentTag = node.parentNode.nodeName;
      if (parentTag === 'SCRIPT' || parentTag === 'STYLE') return NodeFilter.FILTER_REJECT;
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  const touched = [];
  while (walker.nextNode()) touched.push(walker.currentNode);
  touched.forEach(node => {
    const raw = node.nodeValue;
    const trimmed = raw.trim();
    const translated = translateLiteral(trimmed);
    if (translated && translated !== trimmed) {
      node.nodeValue = raw.replace(trimmed, translated);
    }
  });
}

function formatFileCount(count) {
  return langText(`${(count ?? 0).toLocaleString()} 个文件`, `${(count ?? 0).toLocaleString()} files`);
}

function formatAgeText(hours) {
  if (hours == null) return '—';
  if (hours < 1) return langText('<1小时', '<1h');
  if (hours < 24) return langText(`${Math.round(hours)}小时`, `${Math.round(hours)}h`);
  return langText(`${Math.round(hours / 24)}天`, `${Math.round(hours / 24)}d`);
}

function formatPostCount(n) {
  return langText(`发帖 ${n} 篇`, `Posts ${n}`);
}

function formatCurationCount(n) {
  return langText(`策展 ${n} 条`, `Curations ${n}`);
}

function formatTradeCount(n) {
  return langText(`交易 ${n} 笔`, `Trades ${n}`);
}

function formatClaimCount(n) {
  return langText(`领取 ${n} 次`, `Claims ${n}`);
}

function formatResourcePair(op, vp) {
  return langText(`操作权力（点） ${op} · 投票权 ${vp}`, `Operation Power (Point) ${op} · Voting Power ${vp}`);
}

function formatDecisionReason(reason) {
  if (!reason) return '—';
  const raw = String(reason).trim();
  const m = raw.match(/^(.*?)(?:;\s*)?OP\s*([0-9.]+)\s*->\s*([0-9.]+)\s*,\s*VP\s*([0-9.]+)\s*->\s*([0-9.]+)\s*$/i);
  if (!m) return raw;
  const prefix = (m[1] || '').trim();
  const forecast = langText(
    `计划后预测：操作权力（点） ${m[2]}→${m[3]} · 投票权 ${m[4]}→${m[5]}`,
    `Forecast after plan: Operation Power (Point) ${m[2]}→${m[3]} · Voting Power ${m[4]}→${m[5]}`,
  );
  return [prefix || null, forecast].filter(Boolean).join('\n');
}

function formatPortfolioUsd(usd) {
  return langText(`持仓 $${usd}`, `Portfolio $${usd}`);
}

function formatCharCount(n) {
  return langText(`${n} 字`, `${n} chars`);
}

function applyLang() {
  document.documentElement.lang = _lang === 'zh' ? 'zh' : 'en';
  document.title = _lang === 'zh' ? '@clawdbot · 智能体看板' : '@clawdbot · Agent Dashboard';
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll('.lang-seg').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.lang === _lang);
  });
  if (_lastStatus) renderStatus(_lastStatus);
  if (_lastTimeline) {
    renderTimeline(_lastTimeline);
    if (_lastTimeline.summary) renderTimelineSummary(_lastTimeline.summary);
  }
  if (_lastAutoResearch) renderAutoResearch(_lastAutoResearch);
  if (_lastControlTower) renderControlTower(_lastControlTower);
  if (_lastAgentHealth) renderAgentHealth(_lastAgentHealth);
  translateDomTextNodes();
}

function setLang(l) {
  _lang = l;
  localStorage.setItem('tcx_lang', l);
  applyLang();
}

// ── Developer Mode (always on) ───────────────────────────────────────────
document.body.classList.add('dev-mode');

// ── Clock ──────────────────────────────────────────────────────────────────
function updateClock() {
  const el = document.getElementById('clock');
  if (el) el.textContent = new Date().toLocaleString(_lang === 'zh' ? 'zh-CN' : 'en-US', { hour12: false });
}
setInterval(updateClock, 1000);
updateClock();

// ── Helpers ────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

function setText(id, val, fallback = '—') {
  const el = $(id);
  if (el) el.textContent = (val !== null && val !== undefined && val !== '') ? String(val) : fallback;
}

function fmt(n, dec = 2) {
  if (n === null || n === undefined || n === '') return '—';
  return parseFloat(n).toFixed(dec);
}

function fmtNum(n) {
  if (n === null || n === undefined) return '—';
  const v = parseFloat(n);
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return v.toFixed(2);
}

function shortTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    return `${mm}-${dd} ${hh}:${mi}`;
  } catch { return ts.slice(0, 16); }
}

function statusClass(s) {
  if (!s) return '';
  const sl = String(s).toLowerCase();
  if (['bootstrap', 'pending', 'initializing', 'pending_first_run'].some(k => sl.includes(k))) return 'bootstrap';
  if (['ok', 'approve', 'active', 'healthy'].some(k => sl.includes(k))) return 'ok';
  if (['warn', 'partial', 'hold'].some(k => sl.includes(k))) return 'warn';
  if (['error', 'fail', 'reject'].some(k => sl.includes(k))) return 'error';
  return '';
}

function setBadge(id, text, extra) {
  const el = $(id);
  if (!el) return;
  const rawText = text || '—';
  el.textContent = translateLiteral(rawText) || '—';
  el.className = 'badge' + (extra ? ' ' + extra : '');
  const sc = statusClass(rawText);
  if (sc) el.classList.add(sc);
}

function setBar(id, pct, cls) {
  const el = $(id);
  if (!el) return;
  el.style.width = Math.min(100, Math.max(0, pct)) + '%';
  el.className = 'progress-fill ' + (cls || 'ok');
}

function setPill(id, status) {
  const el = $(id);
  if (!el) return;
  el.className = 'pill ' + statusClass(status);
}

function listHtml(items) {
  if (!items || !items.length) return `<div class="muted small">${t('no-data')}</div>`;
  return items.map(it => {
    const title = translateLiteral(it.title || it.text || '');
    const sub = translateLiteral(it.sub || '');
    const right = translateLiteral(it.right || '');
    return `
    <div class="list-item">
      <div class="item-left">
        <div class="item-title">${escHtml(title)}</div>
        ${sub ? `<div class="item-sub">${escHtml(sub)}</div>` : ''}
      </div>
      ${right ? `<div class="item-right">${escHtml(right)}</div>` : ''}
    </div>`;
  }).join('');
}

function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── cleanSummary: replace technical jargon in timeline notes ──────────────
function cleanSummary(text) {
  if (!text) return '—';
  return text
    .replace(/conservative_explore/g, langText('保守探索', 'Conservative Explore'))
    .replace(/reinforce_previous_strategy/g, langText('延续策略', 'Reinforce Strategy'))
    .replace(/discard_previous_strategy/g, langText('切换策略', 'Switch Strategy'))
    .replace(/vp-flush/g, langText('消耗投票权', 'Use voting power'))
    .replace(/\bOP\b/g, langText('操作权力（点）', 'Operation Power (Point)'))
    .replace(/\bVP\b/g, langText('投票权', 'Voting Power'));
}

// ── agentStatusDot: freshness → emoji dot ────────────────────────────────
function agentStatusDot(freshness) {
  if (!freshness) return '⚫';
  if (freshness === 'bootstrap') return '🔵';
  if (freshness === 'fresh') return '🟢';
  if (freshness === 'aging') return '🟡';
  if (freshness === 'stale') return '🟠';
  if (freshness === 'critical') return '🔴';
  return '⚫';
}

function showError(msg) {
  const bar = $('error-bar');
  if (!bar) return;
  bar.textContent = msg;
  bar.classList.remove('hidden');
  setTimeout(() => bar.classList.add('hidden'), 6000);
}

function numericOrNull(v) {
  if (v === null || v === undefined || v === '' || v === 'partial') return null;
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

function minutesAgo(ts) {
  if (!ts) return null;
  try {
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? null : (Date.now() - d.getTime()) / 60000;
  } catch { return null; }
}

function hoursAgoText(ts) {
  const mins = minutesAgo(ts);
  if (mins === null) return '—';
  if (mins < 60) return Math.round(mins) + 'm';
  if (mins < 1440) return Math.round(mins / 60) + 'h';
  return Math.round(mins / 1440) + 'd';
}

// ── API ────────────────────────────────────────────────────────────────────
async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

// ── Agent Tab Switching ───────────────────────────────────────────────────
function switchAgentTab(tab) {
  document.querySelectorAll('.agent-tab').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tab));
  document.querySelectorAll('.agent-tab-content').forEach(el => el.classList.toggle('active', el.id === 'tab-' + tab));
}

// ── Card Tab Switch (Treasury Policy / Social Intent) ─────────────────────
function switchCardTab(el, tabKey) {
  const card = el.closest('.detail-card');
  if (!card) return;
  card.querySelectorAll('.card-tab').forEach(t => t.classList.toggle('active', t.dataset.cardTab === tabKey));
  card.querySelectorAll('.card-tab-body').forEach(b => b.classList.toggle('active', b.dataset.cardBody === tabKey));
}

// ── Section Toggle ────────────────────────────────────────────────────────
function toggleSection(id) {
  const coll = $(id + '-collapsible');
  const btn = $(id + '-toggle');
  if (!coll) return;
  const isOpen = coll.classList.toggle('open');
  if (btn) btn.textContent = isOpen ? '▲' : '▼';
}

// ── Metric Expand/Collapse Toggle ────────────────────────────────────────
document.addEventListener('click', function(e) {
  const row = e.target.closest('.metric-expandable');
  if (!row) return;
  const metric = row.dataset.metric;
  const chevron = document.getElementById('chevron-' + metric);
  const detail = document.getElementById('detail-' + metric);
  if (!detail) return;
  const isOpen = detail.classList.toggle('open');
  if (chevron) chevron.classList.toggle('open', isOpen);
});

// ── Sparkline SVG ─────────────────────────────────────────────────────────
// values: array of numbers, timestamps: optional array of ISO strings (same length)
// statuses: optional array of status strings (same length)
// - 'ok' => canonical solid point
// - non-ok + numeric value => hollow ring (degraded but value present)
// - non-ok + null value => explicit gap marker (do not connect the line across the gap)
function sparklineSvg(values, color, w = 230, h = 60, timestamps, statuses) {
  const leftPad = 28, rightPad = 6, padY = 6, botPad = 18;
  const chartW = w - leftPad - rightPad;
  const chartH = h - padY - botPad;
  const pts = values.map((v, i) => ({ v: numericOrNull(v), i, status: (statuses && statuses[i]) || 'ok' }));
  const valid = pts.filter(p => p.v !== null);
  if (!valid.length) return `<svg viewBox="0 0 ${w} ${h}" class="tas-sparkline"><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="rgba(255,255,255,0.28)" font-size="9">${t('no-tas-history')}</text></svg>`;

  const rawMin = Math.min(...valid.map(p => p.v));
  const rawMax = Math.max(...valid.map(p => p.v));
  const span = Math.max(rawMax - rawMin, 0.01);
  const yMin = Math.max(0, rawMin - span * 0.15);
  const yMax = rawMax + span * 0.15;
  const ySpan = Math.max(yMax - yMin, 0.001);
  const count = Math.max(values.length - 1, 1);
  const toX = i => leftPad + (i / count) * chartW;
  const toY = v => padY + chartH - ((v - yMin) / ySpan) * chartH;
  const baseY = padY + chartH;
  const hexToRgb = h => { const r = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(h); return r ? `${parseInt(r[1],16)},${parseInt(r[2],16)},${parseInt(r[3],16)}` : '255,255,255'; };
  const rgb = hexToRgb(color);

  const segments = [];
  let currentSeg = [];
  pts.forEach(p => {
    if (p.v === null) {
      if (currentSeg.length) segments.push(currentSeg);
      currentSeg = [];
      return;
    }
    currentSeg.push(p);
  });
  if (currentSeg.length) segments.push(currentSeg);

  const areaPolys = segments
    .filter(seg => seg.length >= 2)
    .map(seg => {
      const linePts = seg.map(p => `${toX(p.i).toFixed(1)},${toY(p.v).toFixed(1)}`);
      const areaPts = `${linePts.join(' ')} ${toX(seg[seg.length - 1].i).toFixed(1)},${baseY.toFixed(1)} ${toX(seg[0].i).toFixed(1)},${baseY.toFixed(1)}`;
      return `<polygon points="${areaPts}" fill="url(#sg-${rgb.replace(/,/g,'-')})"/>`;
    }).join('');

  const lineSegs = segments.map(seg => {
    const linePts = seg.map(p => `${toX(p.i).toFixed(1)},${toY(p.v).toFixed(1)}`);
    return `<polyline fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" points="${linePts.join(' ')}"/>`;
  }).join('');

  const markers = pts.map(p => {
    const x = toX(p.i).toFixed(1);
    if (p.v === null) {
      if (!p.status || p.status === 'ok') return '';
      const y = (baseY - 2).toFixed(1);
      return `<line x1="${x}" y1="${padY}" x2="${x}" y2="${baseY.toFixed(1)}" stroke="${color}" stroke-width="0.8" stroke-dasharray="2,2" opacity="0.16"/>`
        + `<circle cx="${x}" cy="${y}" r="2.6" fill="none" stroke="${color}" stroke-width="1" opacity="0.5"><title>${p.status}</title></circle>`;
    }
    const y = toY(p.v).toFixed(1);
    const isOk = !p.status || p.status === 'ok';
    if (isOk) return `<circle cx="${x}" cy="${y}" r="2" fill="${color}" opacity="0.9"/>`;
    return `<circle cx="${x}" cy="${y}" r="2.5" fill="none" stroke="${color}" stroke-width="1" opacity="0.45"><title>${p.status}</title></circle>`;
  }).join('');

  // Y-axis labels (min, mid, max)
  const yMid = (rawMin + rawMax) / 2;
  const yLabels = [
    `<text x="${leftPad - 3}" y="${toY(rawMax).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="6.5" fill="rgba(255,255,255,0.4)">${rawMax.toFixed(2)}</text>`,
    `<text x="${leftPad - 3}" y="${toY(yMid).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="6.5" fill="rgba(255,255,255,0.25)">${yMid.toFixed(2)}</text>`,
    `<text x="${leftPad - 3}" y="${toY(rawMin).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="6.5" fill="rgba(255,255,255,0.4)">${rawMin.toFixed(2)}</text>`,
  ].join('');
  // Horizontal grid lines
  const gridLines = [rawMax, yMid, rawMin].map(v =>
    `<line x1="${leftPad}" y1="${toY(v).toFixed(1)}" x2="${(leftPad + chartW).toFixed(1)}" y2="${toY(v).toFixed(1)}" stroke="rgba(255,255,255,0.06)" stroke-width="0.5" stroke-dasharray="3,3"/>`
  ).join('');

  // X-axis time labels from timestamps
  let xLabels = '';
  if (timestamps && timestamps.length >= 2) {
    const fmtTs = (iso) => { try { const d = new Date(iso); return (d.getMonth()+1) + '/' + d.getDate() + ' ' + String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0'); } catch { return ''; } };
    const first = fmtTs(timestamps[0]);
    const last = fmtTs(timestamps[timestamps.length - 1]);
    const labelY = (baseY + 12).toFixed(1);
    xLabels = `<text x="${leftPad}" y="${labelY}" font-size="6" fill="rgba(255,255,255,0.35)">${first}</text>` +
              `<text x="${(leftPad + chartW).toFixed(1)}" y="${labelY}" text-anchor="end" font-size="6" fill="rgba(255,255,255,0.35)">${last}</text>`;
    if (timestamps.length >= 5) {
      const midIdx = Math.floor(timestamps.length / 2);
      const mid = fmtTs(timestamps[midIdx]);
      xLabels += `<text x="${toX(midIdx).toFixed(1)}" y="${labelY}" text-anchor="middle" font-size="6" fill="rgba(255,255,255,0.25)">${mid}</text>`;
    }
  }

  // Y-axis title
  const yTitle = `<text x="4" y="${(padY + chartH / 2).toFixed(1)}" font-size="5.5" fill="rgba(255,255,255,0.3)" transform="rotate(-90,4,${(padY + chartH / 2).toFixed(1)})" dominant-baseline="middle">TAS</text>`;

  return `<svg viewBox="0 0 ${w} ${h}" class="tas-sparkline">
    <defs><linearGradient id="sg-${rgb.replace(/,/g,'-')}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${color}" stop-opacity="0.25"/><stop offset="100%" stop-color="${color}" stop-opacity="0.03"/></linearGradient></defs>
    ${gridLines}
    <line x1="${leftPad}" y1="${baseY.toFixed(1)}" x2="${(leftPad + chartW).toFixed(1)}" y2="${baseY.toFixed(1)}" stroke="rgba(255,255,255,0.1)" stroke-width="1"/>
    ${areaPolys}
    ${lineSegs}
    ${markers}
    ${yLabels}
    ${yTitle}
    ${xLabels}
  </svg>`;
}

// ── Trend Helpers ─────────────────────────────────────────────────────────
function trendArrow(current, previous) {
  if (current == null || previous == null) return { arrow: '→', cls: 'trend-flat' };
  const diff = current - previous;
  if (diff > 0.01) return { arrow: '↑', cls: 'trend-up' };
  if (diff < -0.01) return { arrow: '↓', cls: 'trend-down' };
  return { arrow: '→', cls: 'trend-flat' };
}

function strategyBadgeCls(action) {
  const a = String(action || '').toLowerCase();
  if (a.includes('reinforce')) return 'strategy-reinforce';
  if (a.includes('discard') || a.includes('regress')) return 'strategy-discard';
  if (a.includes('explore') || a.includes('hold')) return 'strategy-explore';
  return '';
}

/**
 * Derive a display-safe verdict that accounts for TAS delta direction.
 * The raw verdict comes from domain-specific evaluation (curator reward metrics),
 * but when shown next to tas_delta, "reinforce" + negative delta is misleading.
 */
function deltaAwareVerdict(rawVerdict, tasDelta) {
  if (tasDelta == null) return rawVerdict;
  const v = String(rawVerdict || '').toLowerCase();
  if (v.includes('reinforce')) {
    if (tasDelta < -0.01) return 'regressing';       // negative delta ≠ reinforcing
    if (Math.abs(tasDelta) <= 0.01) return 'holding'; // flat
    return rawVerdict;                                 // positive delta: genuine reinforce
  }
  return rawVerdict;
}

// ══════════════════════════════════════════════════════════════════════════
// RENDER: Status
// ══════════════════════════════════════════════════════════════════════════
function renderStatus(data) {
  const rs = data.runtime_status || {};
  // Agent pills with age
  ['main', 'bookmarker', 'trader'].forEach(a => {
    const info = rs[a] || {};
    setPill('pill-' + a, info.status || '');
    const ageEl = $('pill-' + a + '-age');
    if (ageEl) {
      const updatedAt = info.updated_at || info.last_heartbeat;
      ageEl.textContent = updatedAt ? hoursAgoText(updatedAt) : '';
      const mins = minutesAgo(updatedAt);
      if (mins !== null && mins > 180) ageEl.classList.add('stale');
      else ageEl.classList.remove('stale');
    }
  });

  const tas = (data.main || {}).tas_latest || {};
  const history = (data.main || {}).tas_history || [];
  const tasTotal = numericOrNull(tas.tas_total);
  const tasSocial = numericOrNull(tas.tas_social);
  const tasTrade = numericOrNull(tas.tas_trade);

  // Header TAS
  setText('tas-total', tasTotal != null ? fmt(tasTotal) : '—');
  const totalEl = $('tas-total');
  if (totalEl) totalEl.className = 'value ' + (tasTotal == null ? 'clr-bootstrap' : tasTotal >= 1.5 ? 'clr-ok' : tasTotal >= 0.8 ? 'clr-warn' : 'clr-error');

  // Trend arrow from history
  const prevTotal = history.length >= 2 ? numericOrNull(history[history.length - 2].tas_total) : null;
  const trend = trendArrow(tasTotal, prevTotal);
  const trendEl = $('tas-trend-arrow');
  if (trendEl) { trendEl.textContent = trend.arrow; trendEl.className = 'trend-arrow ' + trend.cls; }

  // Strategy badge in header
  const lastDec = (data.main || {}).last_decision || {};
  const sp = (data.main || {}).strategy_plan || {};
  const stratAction = sp.strategy_action || lastDec.strategy_action || '—';
  const stratBadgeEl = $('strategy-badge');
  if (stratBadgeEl) {
    stratBadgeEl.textContent = operatorLang(stratAction);
    stratBadgeEl.className = 'strategy-badge ' + strategyBadgeCls(stratAction);
  }

  renderTasCommandCenter(data);
  renderAgentDetails(data);
  renderWikiModule(data.wiki_system || {});
}

// ── TAS Metric Detail Pane Helpers ──────────────────────────────────────
function _detailRow(label, value) {
  return `<div class="detail-row"><span class="dl">${escHtml(label)}</span><span class="dv">${escHtml(String(value ?? '—'))}</span></div>`;
}

function _populateAlignDetail(sd) {
  const el = $('detail-align');
  if (!el) return;
  const ta = sd.track_a_detail || {};
  const signals = sd.align_signals;
  const posts = sd.eligible_posts || [];
  const interactions = sd.post_interaction_details || [];
  let html = '<div class="detail-label">Align Track — @0xNought interactions</div>';
  html += _detailRow('scorer', ta.scorer || '@0xNought');
  html += _detailRow('window', (ta.window_hours || 24) + 'h rolling');
  if (typeof signals === 'object' && signals !== null) {
    html += _detailRow('signals', `L:${signals.like??0} C:${signals.curation??0} R:${signals.retweet??0} cmt:${signals.comment??0}`);
  } else {
    html += _detailRow('raw_align', signals ?? ta.raw_align ?? 0);
  }
  html += _detailRow('normalization', 'raw / 4.0 capped at 5.0');
  if (ta.fallback_rule) html += `<div style="color:var(--muted);font-size:.6rem;margin-top:.15rem">${escHtml(ta.fallback_rule)}</div>`;
  if (posts.length) {
    html += '<div class="detail-posts detail-label" style="margin-top:.25rem">Recent posts (' + posts.length + ')</div>';
    const pidSet = new Set(interactions.filter(i => i['0xNought_found']).map(i => i.post_id));
    posts.slice(0, 4).forEach(p => {
      const ownerTag = pidSet.has(p.id) ? ' ✓owner' : '';
      html += `<div class="detail-post"><span class="post-snippet">${escHtml((p.content || '').slice(0, 60))}</span><span class="post-stats">❤${p.likes||0} 🔁${p.retweets||0} 💬${p.replies||0}${ownerTag}</span></div>`;
    });
  }
  el.innerHTML = html;
}

function _populateCommunityDetail(sd) {
  const el = $('detail-community');
  if (!el) return;
  const cs = sd.community_signals || {};
  let html = '<div class="detail-label">Community Track — all users</div>';
  html += _detailRow('total_likes', cs.total_likes ?? '—');
  html += _detailRow('total_retweets', cs.total_retweets ?? '—');
  html += _detailRow('total_replies', cs.total_replies ?? '—');
  html += _detailRow('total_interactions', cs.total_interactions ?? '—');
  html += _detailRow('normalization', 'interactions / 20 × 5.0 capped at 5.0');
  html += _detailRow('source', sd.community_source || '—');
  el.innerHTML = html;
}

function _populatePobDetail(sd) {
  const el = $('detail-pob');
  if (!el) return;
  const tc = sd.track_c_detail || {};
  let html = '<div class="detail-label">PoB Reward Track</div>';
  html += _detailRow('pob_claimable_usd', '$' + fmt(sd.pob_claimable_usd ?? tc.pob_claimable_usd));
  html += _detailRow('baseline_usd', '$' + fmt(tc.baseline_usd ?? 5));
  html += _detailRow('pob_reward_score', fmt(sd.pob_reward_score ?? tc.pob_reward_score));
  html += _detailRow('curate_reward_usd', '$' + fmt(sd.curate_reward_usd));
  html += _detailRow('creator_reward_usd', '$' + fmt(sd.creator_reward_usd));
  if (tc.note) html += `<div style="color:var(--muted);font-size:.6rem;margin-top:.15rem">${escHtml(tc.note)}</div>`;
  el.innerHTML = html;
}

function _populatePortfolioNormDetail(td) {
  const el = $('detail-portfolio-norm');
  if (!el) return;
  let html = '<div class="detail-label">Portfolio Normalization</div>';
  html += _detailRow('portfolio_usd_raw', '$' + fmt(td.portfolio_usd_raw));
  html += _detailRow('baseline', '$50');
  html += _detailRow('formula', 'min(portfolio_usd / 50, 1.0)');
  html += _detailRow('weight in TAS_trade', '0.9');
  html += _detailRow('portfolio_value_score', fmt(td.portfolio_value_score));
  const mq = td.measurement_quality || {};
  html += _detailRow('measurement_quality', mq.overall_status || '—');
  html += _detailRow('confidence', mq.overall_confidence ?? '—');
  html += _detailRow('holding_trend', td.holding_trend || '—');
  el.innerHTML = html;
}

function _populateClaimableNormDetail(td) {
  const el = $('detail-claimable-norm');
  if (!el) return;
  let html = '<div class="detail-label">Claimable Normalization</div>';
  html += _detailRow('claimable_usd_raw', '$' + fmt(td.claimable_usd_raw));
  html += _detailRow('baseline', '$5');
  html += _detailRow('formula', 'min(claimable_usd / 5, 1.0)');
  html += _detailRow('weight in TAS_trade', '0.1');
  html += _detailRow('claim_history_score', fmt(td.claim_history_score));
  html += _detailRow('recommended_actions', (td.recommended_actions || []).join(', ') || '—');
  el.innerHTML = html;
}

function _populatePobRewardDetail(td) {
  const el = $('detail-pob-reward');
  if (!el) return;
  let html = '<div class="detail-label">Credit & Strategy Detail</div>';
  html += _detailRow('credit_rank_score', fmt(td.credit_rank_score));
  html += _detailRow('credit_strategy', td.credit_strategy || '—');
  html += _detailRow('holding_trend_score', fmt(td.holding_trend_score));
  html += _detailRow('stake_eligible', td.stake_eligible ? 'yes' : 'no');
  html += _detailRow('lp_eligible', td.lp_eligible ? 'yes' : 'no');
  html += _detailRow('strategy_action', td.strategy_action || '—');
  if (td.planning_focus) html += `<div style="color:var(--muted);font-size:.6rem;margin-top:.15rem">${escHtml(td.planning_focus)}</div>`;
  el.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════════════════
// Section 1: TAS Command Center
// ══════════════════════════════════════════════════════════════════════════
function renderTasCommandCenter(data) {
  const tas = (data.main || {}).tas_latest || {};
  const history = (data.main || {}).tas_history || [];
  const tradeD = (data.trader || {}).tas_trade || {};
  const bm = data.bookmarker || {};
  const trader = data.trader || {};
  const socialDetail = bm.tas_social_detail || {};

  const social = numericOrNull(tas.tas_social);
  const trade = numericOrNull(tas.tas_trade);
  const total = numericOrNull(tas.tas_total);

  // Previous values from history
  const prev = history.length >= 2 ? history[history.length - 2] : {};

  // Center column
  const ccTotal = $('cc-tas-total');
  if (ccTotal) {
    ccTotal.textContent = total !== null ? fmt(total) : '—';
    ccTotal.className = 'big-num jumbo ' + (total >= 1.5 ? 'clr-ok' : total >= 0.8 ? 'clr-warn' : 'clr-error');
  }
  const trendT = trendArrow(total, numericOrNull(prev.tas_total));
  const trendTEl = $('cc-tas-total-trend');
  if (trendTEl) { trendTEl.textContent = trendT.arrow; trendTEl.className = 'trend-arrow ' + trendT.cls; }

  const lastDec = (data.main || {}).last_decision || {};
  const sp = (data.main || {}).strategy_plan || {};
  const stratAction = sp.strategy_action || lastDec.strategy_action || '—';
  const stratEl = $('cc-strategy-action');
  if (stratEl) {
    stratEl.innerHTML = `<span class="strategy-badge ${strategyBadgeCls(stratAction)}">${escHtml(operatorLang(stratAction))}</span>`;
  }
  setText('cc-planning-focus', sp.hypothesis || lastDec.planning_focus || '—');

  // Sparkline
  const sparkEl = $('cc-sparkline');
  let pts = [];
  if (sparkEl) {
    const cutoffMs = Date.now() - 7 * 24 * 60 * 60 * 1000;
    pts = history.filter(p => { try { return new Date(p.ts).getTime() >= cutoffMs; } catch { return false; } });
    if (pts.length < 5) pts = history.slice(-12);
    const recomputeStatuses = () => ({
      total: pts.map(p => p.status || 'ok'),
      social: pts.map(p => (numericOrNull(p.tas_social) === null ? (p.status || 'missing') : 'ok')),
      trade: pts.map(p => (numericOrNull(p.tas_trade) === null ? (p.status || 'missing') : 'ok')),
    });
    const st = recomputeStatuses();
    sparkEl.innerHTML = sparklineSvg(pts.map(p => p.tas_total), '#00d26a', 300, 70, pts.map(p => p.ts), st.total);
    pts._metricStatuses = st;
  }
  // P3/P5: explain degraded aggregate points more explicitly — a dip can be a lower-bound sample
  // caused by one missing component, while component charts may show a gap marker instead of a drop.
  const legendEl = $('cc-sparkline-legend');
  if (legendEl) {
    const degradedCount = pts.filter(p => p.status && p.status !== 'ok').length;
    legendEl.textContent = degradedCount > 0
      ? (_lang === 'zh' ? `${degradedCount} 个降级/不完整聚合点：空心圆=值存在但降级；断点标记=该分量当轮缺失，不参与趋势计算` : `${degradedCount} degraded aggregate point(s): hollow dots = degraded value present; gap markers = component missing, excluded from trend`)
      : '';
  }

  // Bookmarker column (left)
  const ccSocial = $('cc-tas-social');
  if (ccSocial) {
    ccSocial.textContent = social !== null ? fmt(social) : '—';
    ccSocial.className = 'big-num ' + (social >= 1.5 ? 'clr-ok' : social >= 0.8 ? 'clr-warn' : 'clr-error');
  }
  const trendS = trendArrow(social, numericOrNull(prev.tas_social));
  const trendSEl = $('cc-tas-social-trend');
  if (trendSEl) { trendSEl.textContent = trendS.arrow; trendSEl.className = 'trend-arrow small ' + trendS.cls; }

  setText('cc-align-score', fmt(numericOrNull(socialDetail.align_score)));
  setText('cc-community-score', fmt(numericOrNull(socialDetail.community_score)));
  setText('cc-pob-score', fmt(numericOrNull(socialDetail.pob_reward_score != null ? socialDetail.pob_reward_score : socialDetail.curate_reward_score)));
  setText('cc-social-formula', socialDetail.formula || 'TAS_social = 0.5×align + 0.2×community + 0.3×pob×5');
  // Track B source explainer
  const tbDetail = socialDetail.track_b_detail || {};
  const tbSrcEl = $('cc-trackb-source');
  if (tbSrcEl) {
    const capNote = tbDetail.is_capped ? (' [capped]') : '';
    tbSrcEl.textContent = (tbDetail.post_count != null ? `${tbDetail.post_count} @clawdbot posts${capNote}` : '—');
  }

  // ── TAS_social detail panes ──
  _populateAlignDetail(socialDetail);
  _populateCommunityDetail(socialDetail);
  _populatePobDetail(socialDetail);

  // TAS_social sparkline (same style as Aggregated TAS)
  const socialSparkEl = $('cc-social-sparkline');
  if (socialSparkEl && pts.length) {
    const socialStatuses = (pts._metricStatuses && pts._metricStatuses.social) || pts.map(p => (numericOrNull(p.tas_social) === null ? (p.status || 'missing') : 'ok'));
    socialSparkEl.innerHTML = sparklineSvg(pts.map(p => p.tas_social), '#58a6ff', 300, 70, pts.map(p => p.ts), socialStatuses);
  }
  // P4 2026-04-10: explain TAS_social flat lines — when the last N points have identical values,
  // the 24h rolling window likely has no new owner-interaction delta.
  const flatNoteEl = $('cc-social-flat-note');
  if (flatNoteEl) {
    const socialVals = pts.map(p => numericOrNull(p.tas_social)).filter(v => v !== null);
    const tailLen = Math.min(socialVals.length, 5);
    const tail = socialVals.slice(-tailLen);
    const isFlat = tailLen >= 3 && tail.every(v => v === tail[0]);
    flatNoteEl.textContent = isFlat
      ? (_lang === 'zh' ? '24h 无新 owner 互动变化，TAS_social 持平属正常行为' : '24h unchanged — no new owner-interaction delta in rolling window')
      : '';
  }

  // Trader column (right)
  // P3: detect degraded TAS_trade from trader runtime (price_visibility / measurement_quality)
  const _mq = (data.trader || {}).measurement_quality || {};
  const _tradeIsDegraded = tradeD.status === 'degraded'
    || (_mq.price_visibility && _mq.price_visibility !== 'ok')
    || (_mq.overall_status && _mq.overall_status !== 'ok')
    || ((tradeD.measurement_quality || {}).overall_status && (tradeD.measurement_quality || {}).overall_status !== 'ok');
  const ccTrade = $('cc-tas-trade');
  if (ccTrade) {
    ccTrade.textContent = trade !== null ? (fmt(trade) + (_tradeIsDegraded ? ' ⚠' : '')) : '—';
    ccTrade.className = 'big-num ' + (trade >= 1.5 ? 'clr-ok' : trade >= 0.8 ? 'clr-warn' : 'clr-error') + (_tradeIsDegraded ? ' degraded' : '');
  }
  const trendTr = trendArrow(trade, numericOrNull(prev.tas_trade));
  const trendTrEl = $('cc-tas-trade-trend');
  if (trendTrEl) { trendTrEl.textContent = trendTr.arrow; trendTrEl.className = 'trend-arrow small ' + trendTr.cls; }

  setText('cc-portfolio-usd', '$' + fmt(tradeD.portfolio_usd_raw));
  setText('cc-portfolio-norm', fmt(tradeD.portfolio_usd_norm ?? tradeD.base_value));
  setText('cc-claimable-usd', '$' + fmt(tradeD.claimable_usd_raw));
  setText('cc-claimable-norm', fmt(tradeD.claimable_usd_norm));
  setText('cc-pob-reward-score', fmt(tradeD.credit_rank_score));

  // ── TAS_trade detail panes ──
  _populatePortfolioNormDetail(tradeD);
  _populateClaimableNormDetail(tradeD);
  _populatePobRewardDetail(tradeD);

  // TAS_trade sparkline (same style as Aggregated TAS)
  const tradeSparkEl = $('cc-trade-sparkline');
  if (tradeSparkEl && pts.length) {
    const tradeStatuses = (pts._metricStatuses && pts._metricStatuses.trade) || pts.map(p => (numericOrNull(p.tas_trade) === null ? (p.status || 'missing') : 'ok'));
    tradeSparkEl.innerHTML = sparklineSvg(pts.map(p => p.tas_trade), '#f0a500', 300, 70, pts.map(p => p.ts), tradeStatuses);
  }
}

// ══════════════════════════════════════════════════════════════════════════
// Section 2: AutoResearch
// ══════════════════════════════════════════════════════════════════════════
function renderAutoResearch(data) {
  if (!data) return;
  const se = data.strategy_experiment || {};
  const sk = data.skills || {};

  setText('ar-cycle-count', se.cycle_count || '—');
  setText('ar-coupling', se.coupling_alpha != null ? fmt(se.coupling_alpha, 1) : '—');
  setText('cc-cycle-count', se.cycle_count || '—');

  // Track A
  const ta = se.track_a_current_arm || {};
  setText('ar-track-a', [ta.credit_strategy, ta.vp_strategy, ta.target_selection].filter(Boolean).map(v => humanize(v, _lang)).join(' / ') || '—');

  // Track B
  const tb = se.track_b_current_arm || {};
  setText('ar-track-b', [tb.post_timing, tb.engagement_mode].filter(Boolean).map(v => humanize(v, _lang)).join(' / ') || '—');

  // Recent verdicts — delta-aware labels to avoid misleading display
  const vEl = $('ar-verdicts-list');
  if (vEl) {
    const verdicts = se.recent_verdicts || [];
    if (!verdicts.length) {
      vEl.innerHTML = '<div class="muted small">—</div>';
    } else {
      vEl.innerHTML = verdicts.slice(0, 5).map(v => {
        const displayVerdict = deltaAwareVerdict(v.verdict, v.tas_delta);
        const cls = strategyBadgeCls(displayVerdict);
        const delta = v.tas_delta != null ? (v.tas_delta >= 0 ? '+' : '') + fmt(v.tas_delta, 3) : '—';
        const trackLabel = v.track === 'a' ? translateLiteral('A轨') : (v.track === 'b' ? translateLiteral('B轨') : (v.track || ''));
        return `<div class="verdict-row">
          <span class="strategy-badge sm ${cls}">${escHtml(humanize(displayVerdict, _lang))}</span>
          <span class="mono small">${delta}</span>
          <span class="muted small">${escHtml(trackLabel)}</span>
        </div>`;
      }).join('');
    }
  }

  // Skills Tier panel removed — no longer rendered
}

// ══════════════════════════════════════════════════════════════════════════
// Section 3: Agent Details
// ══════════════════════════════════════════════════════════════════════════
function renderAgentDetails(data) {
  renderMainTab(data.main || {});
  renderBookmarkerTab(data.bookmarker || {});
  renderTraderTab(data.trader || {});
  renderDevPanel(data.dev_dispatch || {});
  renderClaudeDispatchTab(data.dev_dispatch || {}, (_lastAgentHealth || {}).agents || {});
}

// ── Main Tab ──
function renderMainTab(main) {
  const si = main.social_intent || {};
  const dec = main.last_decision || {};
  const ba = main.budget_allocation || {};
  const sa = main.social_actions || [];
  const tp = main.strategy_plan || {};

  // Social Intent
  const actions = (si.payload || {}).actions || [];
  const intentEl = $('main-social-intent');
  if (intentEl) {
    if (!actions.length) {
      intentEl.innerHTML = `<div class="muted small">${escHtml(si.reason || si.status || t('no-actions'))}</div>`;
    } else {
      intentEl.innerHTML = listHtml(actions.map(a => ({
        title: a.type || a.action || JSON.stringify(a),
        sub: a.target || a.tick || '',
        right: a.amount ? fmtNum(a.amount) : '',
      })));
    }
  }

  // Treasury Policy
  const tpEl = $('main-treasury-policy');
  if (tpEl) {
    const payload = si.payload || {};
    tpEl.innerHTML = listHtml([
      { title: 'execution_allowed', right: payload.authorized ? '✓' : '✗' },
      { title: 'mode', right: tp.strategy_action || dec.strategy_action || '—' },
      { title: 'coupling', sub: 'align_event_active', right: String(((main.social_intent || {}).meta || {}).coupling_active || false) },
    ]);
  }

  // Decision
  const decText = [
    dec.social_decision ? `${t('decision-social')}: ${dec.social_decision}` : null,
    dec.treasury_decision ? `${t('decision-treasury')}: ${dec.treasury_decision}` : null,
    dec.reason ? formatDecisionReason(dec.reason) : null,
  ].filter(Boolean).join('\n') || '—';
  setText('main-decision', decText);

  // Budget
  const budgetEl = $('main-budget-allocation');
  if (budgetEl) {
    const alloc = ba.allocations || {};
    const live = main.live_resources || {};
    budgetEl.innerHTML = listHtml([
      { title: langText('实时余额', 'Live Balance'), sub: formatResourcePair(fmtNum(live.op), fmtNum(live.vp)), right: langText('实时', 'Live') },
      { title: langText('书签员 / 社交预算', 'Bookmarker / social budget'), sub: formatResourcePair(fmtNum(alloc.bookmarker?.op_budget), fmtNum(alloc.bookmarker?.vp_budget)), right: alloc.bookmarker?.authorized ? 'active' : 'hold' },
      { title: langText('交易员 / 国库', 'Trader / treasury'), sub: langText(`$${fmt(alloc.trader?.usd_budget)} · 风险 ${alloc.trader?.risk_budget || ba.risk_budget || '—'}`, `$${fmt(alloc.trader?.usd_budget)} · risk ${alloc.trader?.risk_budget || ba.risk_budget || '—'}`), right: alloc.trader?.authorized ? 'active' : 'hold' },
      { title: langText('Claude Dispatch / 开发', 'Claude Dispatch / dev'), sub: langText(`槽位 ${alloc.claude_dispatch?.slots ?? ba.dev_budget ?? 0}`, `slots ${alloc.claude_dispatch?.slots ?? ba.dev_budget ?? 0}`), right: ba.dev_budget ? 'on' : 'off' },
    ]);
  }

}

// ── Bookmarker Tab ──
function renderBookmarkerCurationVpPanel(panel) {
  const el = $('bm-curation-vp-panel');
  if (!el) return;
  if (!panel || !panel.total_curations) {
    el.innerHTML = `<div class="muted small">${langText('近 24h 无策展记录', 'No curations in last 24h')}</div>`;
    return;
  }
  const buckets = panel.buckets || [];
  const maxCount = Math.max(1, ...buckets.map(b => Number(b.count || 0)));
  const summaryRows = `
    <div class="kv-compact compact-stack">
      <div class="kv-row"><span class="k">curations_24h</span><span class="v mono">${escHtml(String(panel.total_curations ?? '—'))}</span></div>
      <div class="kv-row"><span class="k">vp_spent_24h</span><span class="v mono">${escHtml(fmt(panel.total_vp_spent))}</span></div>
      <div class="kv-row"><span class="k">avg_vp</span><span class="v mono">${escHtml(fmt(panel.avg_vp))}</span></div>
      <div class="kv-row"><span class="k">VP&gt;1 share</span><span class="v mono">${escHtml(fmt(panel.non_one_share_pct))}%</span></div>
      <div class="kv-row"><span class="k">unique_levels</span><span class="v mono">${escHtml((panel.unique_levels || []).join(', ') || '—')}</span></div>
    </div>`;
  const bars = buckets.map(b => {
    const count = Number(b.count || 0);
    const pct = maxCount > 0 ? (count / maxCount) * 100 : 0;
    return `<div class="heat-bar-row">
      <span class="heat-bar-tick">VP ${b.vp}</span>
      <div class="heat-bar-wrap"><div class="heat-bar-fill hb-stable" style="width:${pct.toFixed(0)}%"></div></div>
      <div class="heat-bar-meta">
        <span class="heat-bar-score">${count}×</span>
        <span class="heat-bar-rank">${fmt(b.share_pct, 1)}%</span>
      </div>
    </div>`;
  }).join('');
  const recent = listHtml((panel.recent || []).map(r => ({
    title: `VP ${r.vp ?? '?'} · ${(r.target_key || r.tweet_id || '?')}`,
    sub: [shortTs(r.executed_at), r.reason].filter(Boolean).join(' · '),
    right: r.cycle_id ? String(r.cycle_id).slice(11, 16) : '',
  })));
  el.innerHTML = `${summaryRows}
    <div class="heat-bars" style="margin-top:.55rem">${bars}</div>
    <div class="section-label" style="margin-top:.65rem">${langText('最近策展', 'Recent curations')}</div>
    ${recent}`;
}

function renderBookmarkerFallbackPreview(panel) {
  const el = $('bm-curation-preview-panel');
  if (!el) return;
  if (!panel || panel.ok === false) {
    const err = panel && panel.error ? String(panel.error) : langText('预览暂不可用', 'Preview unavailable');
    el.innerHTML = `<div class="muted small">${escHtml(err)}</div>`;
    return;
  }
  const candidates = panel.candidates || [];
  const summary = `
    <div class="kv-compact compact-stack">
      <div class="kv-row"><span class="k">candidate_count</span><span class="v mono">${escHtml(String(panel.candidate_count ?? 0))}</span></div>
      <div class="kv-row"><span class="k">updated_at</span><span class="v mono">${escHtml(shortTs(panel.updated_at) || '—')}</span></div>
    </div>`;
  if (!candidates.length) {
    el.innerHTML = `${summary}<div class="muted small" style="margin-top:.55rem">${langText('当前没有可用的 fallback curate 候选', 'No fallback curate candidates right now')}</div>`;
    return;
  }
  const list = listHtml(candidates.map(c => ({
    title: `VP ${c.vp ?? '?'} · ${(c.target_key || c.tweet_id || '?')}`,
    sub: c.reason || c.source || '',
    right: c.tweet_id || '',
  })));
  el.innerHTML = `${summary}
    <div class="section-label" style="margin-top:.65rem">${langText('如果本轮现在策展', 'If curate ran now')}</div>
    ${list}`;
}

function renderBookmarkerTab(bm) {
  const brief = bm.topic_brief || {};
  const cands = bm.content_candidates || {};
  const src = bm.source_health || {};
  const auto = bm.autonomy_intent || {};

  setText('bm-headline', brief.headline || brief.summary || '—');
  const kwEl = $('bm-keywords');
  if (kwEl) {
    const kws = brief.keywords || [];
    kwEl.innerHTML = kws.slice(0, 8).map(k => `<span class="tag">${escHtml(k)}</span>`).join('');
  }

  const rawCandidates = cands.candidates || cands.items || brief.candidates || [];
  const candidateList = rawCandidates.filter(c => c.publish_ready !== false || c.title || c.headline || c.url);
  const candsEl = $('bm-cands-list');
  if (candsEl) {
    candsEl.innerHTML = listHtml(candidateList.slice(0, 5).map(c => ({
      title: c.title || c.headline || c.summary || c.url || JSON.stringify(c).slice(0, 80),
      sub: `${c.type || c.source || ''}${c.recommended_action ? ' · ' + c.recommended_action : ''}`,
      right: c.expected_tas_social_uplift != null ? fmt(c.expected_tas_social_uplift) : '',
    })));
  }

  // Drafts
  const draftsObj = bm.social_drafts || {};
  const drafts = draftsObj.drafts || [];
  const draftsStatusEl = $('bm-drafts-status');
  if (draftsStatusEl) {
    draftsStatusEl.textContent = draftsObj.status || '—';
    draftsStatusEl.className = 'badge sm ' + statusClass(draftsObj.status || '');
  }
  const draftsEl = $('bm-drafts-list');
  if (draftsEl) {
    if (!drafts.length) {
      draftsEl.innerHTML = '<div class="muted small">No drafts</div>';
    } else {
      draftsEl.innerHTML = drafts.map(d => {
        const txt = (d.text || '').slice(0, 100) + ((d.text || '').length > 100 ? '…' : '');
        return `<div class="list-item"><div class="item-left">
          <span class="badge sm">${escHtml(d.type || '?')}</span>
          <span class="badge sm muted">${escHtml(d.tick || '')}</span>
          <span class="item-sub">${escHtml(d.theme || '')}</span>
        </div><div class="item-right"><span class="muted small">p${d.priority || 0}</span></div></div>
        <div class="muted small" style="padding:2px 8px 6px;font-size:.72em;line-height:1.4">${escHtml(txt)}</div>`;
      }).join('');
    }
  }

  // Source health
  const xSync = src.x_sync || {};
  setText('bm-sync-at', shortTs(xSync.fetched_at || src.fetched_at || src.updated_at));
  const autoMode = auto.mode || auto.autonomy_mode || '—';
  setBadge('bm-mode-badge', autoMode, autoMode.toLowerCase().replace(/[^a-z-]/g, ''));

  // Align events
  const alignEl = $('bm-align-events');
  if (alignEl) {
    const mg = auto.main_guidance || {};
    alignEl.innerHTML = listHtml([
      { title: 'has_active_event', right: String(mg.align_event_active || false) },
      { title: 'align_count_24h', right: String(mg.align_count_24h || 0) },
    ]);
  }

  renderBookmarkerCurationVpPanel(bm.curation_vp_24h || {});
  renderBookmarkerFallbackPreview(bm.curation_fallback_preview || {});
  renderPipeline('bm-social-pipeline', bm.social_pipeline, 'bookmarker');

  // Populate merged detail slots inside pipeline step cards
  const pipeXSync = $('bm-pipe-detail-x_sync');
  if (pipeXSync) {
    const modeVal = autoMode && autoMode !== '—' ? autoMode : '';
    pipeXSync.innerHTML = modeVal ? `<div class="kv-compact compact-stack">
        <div class="kv-row"><span class="k" data-i18n="label-mode">mode</span><span class="v mono">${escHtml(modeVal)}</span></div>
      </div>` : '';
  }
  const pipeTopic = $('bm-pipe-detail-topic_brief');
  if (pipeTopic) {
    const kws = [...new Set(brief.keywords || [])].slice(0, 4);
    pipeTopic.innerHTML = `<div class="text-block pipeline-blurb">${escHtml(brief.headline || brief.summary || '—')}</div>
      ${kws.length ? `<div class="tags-row compact-tags">${kws.map(k => `<span class="tag">${escHtml(k)}</span>`).join('')}</div>` : ''}`;
  }
  const pipeCands = $('bm-pipe-detail-content_candidates');
  if (pipeCands) {
    const rawC = cands.candidates || cands.items || brief.candidates || [];
    const cl = rawC.filter(c => c.publish_ready !== false || c.title || c.headline || c.url);
    pipeCands.innerHTML = renderMiniFeed(cl.map(c => ({
      title: c.title || c.headline || c.summary || c.url || JSON.stringify(c).slice(0, 60),
      sub: c.recommended_action || c.type || c.source || '',
      meta: c.url ? 'source link' : '',
      right: c.expected_tas_social_uplift != null ? fmt(c.expected_tas_social_uplift) : '',
    })), { limit: 2 });
  }
  const pipeDrafts = $('bm-pipe-detail-social_drafts');
  if (pipeDrafts) {
    const dObj = bm.social_drafts || {};
    const dList = dObj.drafts || [];
    if (!dList.length) {
      pipeDrafts.innerHTML = '<div class="muted small">No drafts</div>';
    } else {
      pipeDrafts.innerHTML = renderMiniFeed(dList.map(d => ({
        title: (d.text || '').slice(0, 72) + ((d.text || '').length > 72 ? '…' : ''),
        sub: [d.type, d.tick, d.theme].filter(Boolean).join(' · '),
        meta: d.priority != null ? `p${d.priority}` : '',
        right: '',
      })), { limit: 2 });
    }
  }
}

// ── Trader Tab ──
function renderTraderTab(trader) {
  const wallet = trader.wallet_snapshot || {};
  const rewards = trader.reward_status || {};
  const tasT = trader.tas_trade || {};
  const risk = trader.risk_status || {};
  const onchain = trader.onchain_positions || {};

  setText('trader-tas', fmt(tasT.value ?? tasT.score ?? tasT.tas_trade));
  const trMode = tasT.autonomy_mode || '—';
  setBadge('trader-mode-badge', trMode, trMode.toLowerCase().replace(/[^a-z-]/g, ''));
  const riskLvl = risk.level || risk.status || (risk.risk_flags?.length ? 'partial' : 'ok') || '—';
  setBadge('trader-risk-badge', riskLvl);

  // Wallet
  const balEl = $('trader-balances');
  if (balEl) {
    const totalUsd = onchain.total_portfolio_usd;
    const positions = onchain.positions || [];
    if (positions.length) {
      const total = parseFloat(totalUsd || 0);
      const totalRow = totalUsd != null ? `<div class="list-item wallet-total"><div class="item-left"><div class="item-title">${t('total-value')}</div></div><div class="item-right">$${fmt(totalUsd)}</div></div>` : '';
      const rows = positions.map(p => {
        const value = parseFloat(p.value_usd || 0);
        const ratio = total > 0 ? (value / total) : 0;
        const pct = total > 0 ? `${(ratio * 100).toFixed(1)}%` : '—';
        return `<div class="list-item"><div class="item-left"><div class="item-title">${escHtml(p.tick || '')}</div><div class="item-sub">${t('portfolio-share')} ${escHtml(pct)}</div></div><div class="item-right">${escHtml(fmtNum(parseFloat(p.balance)))} ($${escHtml(fmt(p.value_usd))})</div></div>`;
      }).join('');
      balEl.innerHTML = totalRow + rows;
    } else {
      const bals = wallet.balances || {};
      const items = Object.entries(bals).map(([tick, amt]) => ({ title: tick, right: fmtNum(parseFloat(amt)) }));
      balEl.innerHTML = items.length ? listHtml(items) : `<div class="muted small">${t('no-balance')}</div>`;
    }
  }

  // Rewards
  const rewEl = $('trader-rewards');
  if (rewEl) {
    const claimable = rewards.claimable || [];
    if (!claimable.length) {
      rewEl.innerHTML = `<div class="muted small">${t('no-rewards')}</div>`;
    } else {
      rewEl.innerHTML = listHtml(claimable.map(r => ({
        title: r.tick,
        sub: r.status || r.action || '',
        right: `${fmtNum(r.claimable_amount)} ($${fmt(r.reward_value_usd)})`,
      })));
    }
  }

  // Claimable big + threshold bar
  const claimBig = $('trader-claimable-big');
  if (claimBig) {
    const usd = tasT.claimable_usd_raw;
    claimBig.textContent = usd != null ? '$' + fmt(usd) : '—';
    claimBig.className = 'v mono bold ' + (usd >= 2 ? 'clr-ok' : 'clr-warn');
  }
  const claimBar = $('trader-claim-progress');
  if (claimBar) {
    const usd = tasT.claimable_usd_raw || 0;
    const pct = Math.min(100, (usd / 2) * 100);
    claimBar.innerHTML = `<div class="pob-bar-fill ${pct >= 100 ? 'pob-bar-green' : 'pob-bar-yellow'}" style="width:${pct.toFixed(1)}%"></div>`;
  }

  // Risk flags
  const flagEl = $('trader-risk-flags');
  if (flagEl) {
    const flags = risk.risk_flags || risk.reasons || [];
    if (!flags.length) {
      flagEl.innerHTML = `<div class="muted small">${t('no-risk-flags')}</div>`;
    } else {
      flagEl.innerHTML = flags.map(f => `<div class="list-item"><div class="item-title clr-warn">${escHtml(f)}</div></div>`).join('');
    }
  }

  // Community heat badge
  const heatBadgeEl = $('trader-heat-badge');
  if (heatBadgeEl) {
    const wiki = _lastStatus?.wiki_system || {};
    const ch = wiki.community_heat || {};
    const ticks = ch.ticks || {};
    const entries = Object.entries(ticks);
    if (entries.length) {
      heatBadgeEl.innerHTML = entries.map(([tick, v]) => {
        const trend = v.trend || 'stable';
        const arrow = trend === 'rising' ? '↑' : trend === 'declining' ? '↓' : '→';
        const cls = 'trend-' + trend;
        return `<span class="heat-tick ${cls}">${escHtml(tick)} ${arrow}</span>`;
      }).join(' ');
    } else {
      heatBadgeEl.textContent = '—';
    }
  }

  // Coupling
  const coupEl = $('trader-coupling');
  if (coupEl) {
    const bm = _lastStatus?.bookmarker || {};
    const mg = (bm.autonomy_intent || {}).main_guidance || {};
    const active = mg.align_event_active || false;
    coupEl.textContent = translateLiteral(active ? 'active' : 'inactive');
    coupEl.className = 'indicator ' + (active ? 'ind-ok' : 'ind-off');
  }

}

function renderMiniFeed(items, opts = {}) {
  const limit = opts.limit || 2;
  const rows = (items || []).slice(0, limit);
  if (!rows.length) return '<div class="muted small">—</div>';
  return `<div class="mini-feed">${rows.map((item) => {
    const title = escHtml(item.title || '—');
    const sub = item.sub ? `<div class="mf-sub">${escHtml(item.sub)}</div>` : '';
    const meta = item.meta ? `<div class="mf-meta">${escHtml(item.meta)}</div>` : '';
    const right = item.right ? `<div class="mf-right">${escHtml(item.right)}</div>` : '';
    return `<div class="mf-item"><div class="mf-main"><div class="mf-title">${title}</div>${sub}${meta}</div>${right}</div>`;
  }).join('')}</div>`;
}

// ── Pipeline Renderer ──
function renderPipeline(elId, pipeline, agent) {
  const el = $(elId);
  if (!el || !pipeline || !pipeline.steps) { if (el) el.innerHTML = '<div class="muted small">—</div>'; return; }
  const steps = pipeline.steps;
  const mi = (agent === 'bookmarker' && pipeline.main_influence) ? pipeline.main_influence : null;

  let html = '';
  if (mi) {
    const decCls = mi.social_decision === 'authorize' ? 'clr-ok' : 'clr-warn';
    const authIcon = mi.authorized ? '✓' : '✗';
    const authCls = mi.authorized ? 'clr-ok' : 'clr-warn';
    const guidance = mi.guidance || {};
    const gParts = [guidance.action_emphasis, guidance.signal_priority, guidance.experiment_mode].filter(Boolean);
    html += `<div class="pipeline-main-influence floating-bar"><div class="mi-header">
      <span class="mi-label">Main Agent</span>
      <span class="mi-badge ${decCls}">${escHtml(mi.social_decision)}</span>
      <span class="mi-badge ${authCls}">intent ${authIcon}</span>
      ${gParts.length ? `<span class="mi-summary">${escHtml(gParts.join(' · '))}</span>` : ''}
    </div></div>`;
  }

  html += '<div class="pipeline-steps-row">';
  html += steps.map((step, i) => {
    const arrow = i < steps.length - 1 ? '<div class="pipeline-arrow">→</div>' : '';
    const stCls = 'st-' + (step.status || '').toLowerCase().replace(/[^a-z-]/g, '');
    let detail = '';
    let miAnnotation = '';
    let metaLine = '';

    if (agent === 'main' && step.id === 'gate_checks') {
      const checks = step.data || {};
      detail = Object.entries(checks).filter(([k]) => !k.startsWith('_')).map(([k, v]) =>
        `<div class="gate-item"><span class="${v ? 'gate-pass' : 'gate-fail'}">${v ? '✓' : '✗'}</span> ${escHtml(k)}</div>`
      ).join('');
      const passCount = checks._pass_count ?? Object.values(checks).filter(Boolean).length;
      const total = checks._total ?? Object.keys(checks).filter(k => !k.startsWith('_')).length;
      if (total) detail = `<div class="gate-summary">${passCount}/${total} pass</div>` + detail;
    } else if (agent === 'main' && step.id === 'social_intent') {
      const d = step.data || {};
      detail = `authorized: ${d.authorized ? '✓' : '✗'}`;
      if (d.action_count) detail += ` · ${d.action_count} directives`;
      if (d.reason) detail += `<br>${escHtml(d.reason.slice(0, 80))}`;
    } else if (agent === 'main' && step.id === 'handoff_plane') {
      const d = step.data || {};
      detail = `owner: ${escHtml(d.owner || d.target_agent || 'bookmarker')}`;
      detail += `<br><span class="mono-sm">${escHtml(d.intent_ref || 'runtime/main/social-intent.json')}</span>`;
    } else if (agent === 'main' && step.id === 'feedback_loop') {
      const d = step.data || {};
      detail = escHtml(d.social_decision || '—');
      if (d.feedback_count != null) detail += ` · ${d.feedback_count} feedback`;
      if ((d.ok_count || d.noop_count || d.blocked_count) != null) {
        detail += `<br>ok ${escHtml(String(d.ok_count || 0))} · noop ${escHtml(String(d.noop_count || 0))} · blocked ${escHtml(String(d.blocked_count || 0))}`;
      }
    } else if (agent === 'bookmarker' && step.id === 'x_sync') {
      const d = step.data || {};
      detail = `${escHtml(d.source || d.source_class || '—')}`;
      if (d.updated_at) metaLine = shortTs(d.updated_at);
    } else if (agent === 'bookmarker' && step.id === 'topic_brief') {
      const d = step.data || {};
      const kwCount = (d.keywords || []).length;
      detail = kwCount ? `${kwCount} keywords` : 'topic ready';
    } else if (agent === 'bookmarker' && step.id === 'content_candidates') {
      const d = step.data || {};
      detail = `${d.count || 0} candidates`;
    } else if (agent === 'bookmarker' && step.id === 'social_drafts') {
      const d = step.data || {};
      detail = `${d.count || 0} drafts`;
      const first = ((d.drafts || [])[0] || {});
      if (first.type || first.tick) metaLine = [first.type, first.tick].filter(Boolean).join(' · ');
    } else if (agent === 'bookmarker' && step.id === 'autonomy_intent') {
      const d = step.data || {};
      detail = `${escHtml(d.mode || '—')}`;
      if (d.recommended_actions?.length) metaLine = escHtml(d.recommended_actions.join(', '));
      if (mi) {
        const g = mi.guidance || {};
        const parts = [g.action_emphasis, g.signal_priority, g.experiment_mode].filter(Boolean);
        if (parts.length) miAnnotation = `<div class="mi-annotation"><span class="mi-arrow-in">⤹</span>${escHtml(parts.join(' · '))}</div>`;
        else miAnnotation = `<div class="mi-annotation"><span class="mi-arrow-in">⤹</span>${t('shared-executor')}</div>`;
      }
    } else if (agent === 'bookmarker' && step.id === 'execution') {
      const d = step.data || {};
      const parts = [];
      if (d.attempted) parts.push(`${d.succeeded}/${d.attempted} ok`);
      if (d.failed) parts.push(`${d.failed} fail`);
      detail = parts.join(' · ') || '—';
      const brkState = d.breaker_state || '—';
      const brkCls = brkState === 'open' ? 'clr-error' : 'clr-ok';
      detail += `<br>breaker: <span class="${brkCls}">${escHtml(brkState)}</span>`;
      if (mi) miAnnotation = `<div class="mi-annotation"><span class="mi-arrow-in">⤹</span> ${t('shared-executor')}</div>`;
    }

    // For bookmarker pipeline, embed detail panel inside each step card (merged layout)
    const detailIds = (agent === 'bookmarker') ? ['x_sync', 'topic_brief', 'content_candidates', 'social_drafts', 'autonomy_intent', 'execution'] : [];
    const scrollableIds = ['x_sync', 'topic_brief', 'content_candidates', 'social_drafts'];
    const sid = step.id || '';
    const hasDetail = detailIds.includes(sid);
    const extraCls = (hasDetail && scrollableIds.includes(sid)) ? ' scrollable-panel' : '';
    const detailSlot = hasDetail ? `<div class="pipeline-detail-merged${extraCls}" id="bm-pipe-detail-${sid}"></div>` : '';

    // Step icon map for visual identity
    const stepIcons = { x_sync: '🔄', topic_brief: '📋', content_candidates: '📝', social_drafts: '✏️', autonomy_intent: '🧠', execution: '🚀', gate_checks: '🔒', social_intent: '🎯', handoff_plane: '✈️', feedback_loop: '🔁' };
    const stepIcon = stepIcons[sid] || '';
    const iconSpan = stepIcon ? `<span class="step-icon">${stepIcon}</span>` : '';
    const numChip = `<span class="step-num">${i + 1}</span>`;

    return `<div class="pipeline-step"><div class="pipeline-step-card${miAnnotation ? ' has-mi' : ''} ${stCls}" data-step="${escHtml(sid)}">
      <div class="step-head">
        <div class="step-label">${numChip}${iconSpan}${escHtml(step.label)}</div>
        <div class="step-badge ${stCls}">${escHtml(step.status || '—')}</div>
      </div>
      <div class="step-detail">${detail}</div>
      ${metaLine ? `<div class="step-meta">${metaLine}</div>` : ''}
      ${miAnnotation}
      ${detailSlot}
    </div>${arrow}</div>`;
  }).join('');
  html += '</div>';

  el.innerHTML = html;
}

// ── Dev Panel ──
function renderDevPanel(dev) {
  const status = dev.status || {};
  const result = dev.result || {};
  const roi = dev.dispatch_roi || {};
  const taskIdentity = dev.task_identity || {};
  const currentTask = dev.current_task || null;
  const isRunning = taskIdentity.is_running || false;

  // Status badge: show "running" when active, otherwise latest result status
  if (isRunning) {
    setBadge('dev-status-badge', 'running');
  } else {
    setBadge('dev-status-badge', result.status || status.status || '—');
  }

  // Stage badge: label mismatch if stage refers to different task
  const stageStatus = (dev.stage_status || {}).status || '—';
  setBadge('dev-stage-badge', stageStatus);

  setText('dev-completed-at', isRunning ? '—' : shortTs(result.completed_at || status.updated_at || status.started_at));

  const roiEl = $('dev-dispatch-roi');
  if (roiEl) {
    const mismatch = (taskIdentity.roi_matches_active === false);
    const mismatchNote = mismatch
      ? `<div class="muted small clr-warning">⚠ ${escHtml(t('cd-roi-mismatch'))}</div>`
      : '';
    roiEl.innerHTML = mismatchNote + listHtml([
      { title: 'Target metric', sub: roi.target_metric || '—', right: roi.roi_status || '—' },
      { title: langText('预期 TAS 影响', 'Expected TAS impact'), sub: `${langText('任务', 'task')}: ${roi.task_id || '—'}`, right: roi.expected_tas_impact != null ? fmt(roi.expected_tas_impact) : '—' },
    ]);
  }

  const summaryEl = $('dev-result-summary');
  if (summaryEl) {
    if (isRunning && currentTask) {
      summaryEl.textContent = `[${t('cd-current-task')}] ${currentTask.title || currentTask.task_id || '—'}`;
    } else {
      summaryEl.textContent = result.task_summary || t('no-dev-result');
    }
  }

  const linksEl = $('dev-result-links');
  if (linksEl) {
    const files = result.files_changed || [];
    const summary = result.task_summary || '';
    const links = [];
    if (!isRunning) {
      if (files.some(f => String(f).includes('tools/viz')) || /dashboard/i.test(summary)) {
        links.push({ label: t('live-dashboard'), href: 'https://dashboard.tagclaw.com' });
        links.push({ label: t('github-repo'), href: 'https://github.com/tagai-dao/Tagclaw-dashboard' });
      }
    }
    linksEl.innerHTML = links.length
      ? links.map(l => `<div class="list-item"><div class="item-left"><a class="dev-link" href="${escHtml(l.href)}" target="_blank" rel="noopener noreferrer">${escHtml(l.label)}</a></div></div>`).join('')
      : `<div class="muted small">${escHtml(t('no-links'))}</div>`;
  }
}

// ── Claude Dispatch Tab ──
function renderClaudeDispatchTab(dev, agentHealthAgents) {
  const result = dev.result || {};
  const status = dev.status || {};
  const roi = dev.dispatch_roi || {};
  const stage = dev.stage_status || {};
  const cdAgent = agentHealthAgents.claude_dispatch || {};
  const taskIdentity = dev.task_identity || {};
  const currentTask = dev.current_task || null;

  const isRunning = taskIdentity.is_running || false;
  const activeTaskId = taskIdentity.active_task_id;
  const roiMatchesActive = taskIdentity.roi_matches_active;
  const stageMatchesActive = taskIdentity.stage_matches_active;

  // Determine short task_id for display (last segment after last dash-group)
  function shortTaskId(tid) {
    if (!tid) return '—';
    // Show last ~40 chars for readability
    return tid.length > 40 ? '…' + tid.slice(-38) : tid;
  }

  // Task summary — show current task if running, otherwise latest result
  const summaryEl = $('cd-task-summary');
  if (summaryEl) {
    if (isRunning && currentTask) {
      const label = `<span class="badge sm clr-running">${escHtml(t('cd-current-task'))}</span>`;
      const taskTitle = escHtml(currentTask.title || currentTask.task_id || '—');
      const taskType = currentTask.task_type ? `<span class="badge sm">${escHtml(currentTask.task_type)}</span>` : '';
      const priority = currentTask.priority ? `<span class="badge sm">${escHtml(currentTask.priority)}</span>` : '';
      summaryEl.innerHTML = `${label} ${priority} ${taskType}<br><span class="mono small">${taskTitle}</span>`;
    } else {
      const summary = result.task_summary;
      if (summary) {
        const label = `<span class="badge sm clr-ok">${escHtml(t('cd-latest-result'))}</span>`;
        summaryEl.innerHTML = `${label}<br>${escHtml(summary)}`;
      } else {
        summaryEl.textContent = t('cd-no-task');
      }
    }
    // Show task_id below summary
    if (activeTaskId) {
      const tidHtml = `<div class="muted small mono" style="margin-top:.25rem">${escHtml(t('cd-task-id'))}: ${escHtml(shortTaskId(activeTaskId))}</div>`;
      summaryEl.innerHTML += tidHtml;
    }
  }

  // Files changed — only from result (same task context)
  const filesEl = $('cd-files-changed');
  if (filesEl) {
    const files = result.files_changed || [];
    if (isRunning) {
      // When running, no completed files yet — show placeholder
      filesEl.innerHTML = `<div class="muted small">${escHtml(t('cd-current-task'))} — ${t('no-data')}</div>`;
    } else if (files.length) {
      filesEl.innerHTML = files.map(f => `<div class="list-item"><div class="item-title mono small">${escHtml(f)}</div></div>`).join('');
    } else {
      filesEl.innerHTML = `<div class="muted small">${t('no-data')}</div>`;
    }
  }

  // Test results — only from result context (not mixed with cdAgent)
  if (isRunning) {
    setBadge('cd-result-status-badge', 'running');
    setText('cd-tests-passed', '—');
    setText('cd-pass-count', '—');
    setText('cd-fail-count', '—');
    setText('cd-completed-at', '—');
  } else {
    const resStatus = result.status;
    setBadge('cd-result-status-badge', resStatus || '—');
    const testsPassed = result.tests_passed;
    setText('cd-tests-passed', testsPassed != null ? String(testsPassed) : '—');
    const testResults = result.test_results || {};
    setText('cd-pass-count', testResults.pass != null ? String(testResults.pass) : '—');
    setText('cd-fail-count', testResults.fail != null ? String(testResults.fail) : '—');
    setText('cd-completed-at', shortTs(result.completed_at || status.updated_at));
  }

  // Dispatch ROI — label explicitly if task_id doesn't match active context
  const roiEl = $('cd-dispatch-roi');
  if (roiEl) {
    const roiData = roi;
    const mismatchNote = (roiMatchesActive === false)
      ? `<div class="muted small clr-warning" style="margin-bottom:.25rem">⚠ ${escHtml(t('cd-roi-mismatch'))}: ${escHtml(shortTaskId(taskIdentity.roi_task_id))}</div>`
      : '';
    roiEl.innerHTML = mismatchNote + listHtml([
      { title: 'Target metric', sub: roiData.target_metric || '—', right: roiData.roi_status || '—' },
      { title: langText('预期 TAS 影响', 'Expected TAS impact'), sub: `${langText('任务', 'task')}: ${escHtml(shortTaskId(roiData.task_id))}`, right: roiData.expected_tas_impact != null ? fmt(roiData.expected_tas_impact) : '—' },
    ]);
  }

  // Built tools — from result only
  const toolsEl = $('cd-built-tools');
  if (toolsEl) {
    const tools = result.built_tools || [];
    if (isRunning) {
      toolsEl.innerHTML = `<div class="muted small">—</div>`;
    } else if (tools.length) {
      toolsEl.innerHTML = tools.map(tl => `<div class="list-item"><div class="item-title">${escHtml(tl)}</div></div>`).join('');
    } else {
      toolsEl.innerHTML = `<div class="muted small">${t('no-data')}</div>`;
    }
  }

  // Result links — from result only
  const linksEl = $('cd-result-links');
  if (linksEl) {
    const files = result.files_changed || [];
    const summary = result.task_summary || '';
    const links = [];
    if (!isRunning) {
      if (files.some(f => String(f).includes('tools/viz')) || /dashboard/i.test(summary)) {
        links.push({ label: t('live-dashboard'), href: 'https://dashboard.tagclaw.com' });
        links.push({ label: t('github-repo'), href: 'https://github.com/tagai-dao/Tagclaw-dashboard' });
      }
      const resultLinks = result.result_links || [];
      resultLinks.forEach(l => {
        if (typeof l === 'string') links.push({ label: l, href: l });
        else if (l && l.href) links.push(l);
      });
    }
    linksEl.innerHTML = links.length
      ? links.map(l => `<div class="list-item"><div class="item-left"><a class="dev-link" href="${escHtml(l.href)}" target="_blank" rel="noopener noreferrer">${escHtml(l.label)}</a></div></div>`).join('')
      : `<div class="muted small">${t('no-links')}</div>`;
  }

  // Blockers — from result context
  const blockersEl = $('cd-blockers');
  if (blockersEl) {
    const blockers = result.blockers || [];
    if (blockers.length) {
      blockersEl.innerHTML = blockers.map(b => `<div class="list-item"><div class="item-title clr-error">${escHtml(typeof b === 'string' ? b : JSON.stringify(b))}</div></div>`).join('');
    } else {
      blockersEl.innerHTML = `<div class="muted small clr-ok">${t('no-blockers')}</div>`;
    }
  }
}

// ══════════════════════════════════════════════════════════════════════════
// Section 4: Wiki Intelligence
// ══════════════════════════════════════════════════════════════════════════
function renderWikiModule(wiki) {
  if (!wiki || !wiki.raw_layer) {
    fetchJSON('/api/wiki').then(w => _renderWikiInner(w)).catch(e => console.warn('wiki fetch error:', e));
    return;
  }
  _renderWikiInner(wiki);
}

function _renderWikiInner(wiki) {
  renderRawLayer(wiki);
  renderWikiLayer(wiki);
  renderExecutionBrief(wiki);
  renderIngestMatrix(wiki);
  renderContractHealth(wiki);
  renderAgentWikiStatus(wiki);
  renderCommunityHeatMap(wiki);
}

function _ageText(hours) {
  return formatAgeText(hours);
}

function renderRawLayer(wiki) {
  const raw = wiki.raw_layer || {};
  const el = $('wiki-raw-list');
  const totalEl = $('wiki-raw-total');
  if (totalEl) totalEl.textContent = formatFileCount(raw.total_files ?? 0);
  if (!el) return;
  const subdirs = raw.subdirs || {};
  const keys = Object.keys(subdirs);
  if (!keys.length) { el.innerHTML = '<span class="muted small">no data</span>'; return; }
  el.innerHTML = keys.map(k => {
    const d = subdirs[k];
    return `<div class="wiki-dir-row"><span class="wiki-dir-name" style="opacity:.7">${escHtml(k)}</span><span class="wiki-dir-meta"><span>${d.file_count ?? 0}</span><span class="muted">${_ageText(d.newest_file_age_hours)}</span></span></div>`;
  }).join('');
}

function renderWikiLayer(wiki) {
  const wl = wiki.wiki_layer || {};
  const el = $('wiki-wiki-list');
  const totalEl = $('wiki-wiki-total');
  if (totalEl) totalEl.textContent = formatFileCount(wl.total_files ?? 0);
  if (!el) return;
  const subdirs = wl.subdirs || {};
  const keys = Object.keys(subdirs);
  if (!keys.length) { el.innerHTML = '<span class="muted small">no data</span>'; return; }
  el.innerHTML = keys.map(k => {
    const d = subdirs[k];
    return `<div class="wiki-dir-row"><span class="wiki-dir-name">${escHtml(k)}</span><span class="wiki-dir-meta"><span>${d.file_count ?? 0}</span><span class="muted">${_ageText(d.newest_file_age_hours)}</span><span class="wiki-dir-tag">${escHtml(d.role || 'compiled')}</span></span></div>`;
  }).join('');

  const lintEl = $('wiki-lint-inline');
  if (lintEl) {
    const lint = wiki.lint || {};
    const broken = lint.broken_links_count || 0;
    const stale = lint.stale_count || 0;
    const orphan = lint.orphan_count || 0;
    const brokenCls = broken > 0 ? 'clr-error' : 'clr-ok';
    const staleCls = stale > 0 ? 'clr-warn' : 'clr-ok';
    const orphanCls = orphan > 0 ? 'clr-warn' : 'clr-ok';
    const hs = lint.health_score != null ? `<span class="${lint.health_score >= 80 ? 'clr-ok' : lint.health_score >= 60 ? 'clr-warn' : 'clr-error'}" title="${langText('健康分', 'Health score')}">${lint.health_score.toFixed(0)}%</span> · ` : '';
    const ts = lint.generated_at ? `<span class="muted small" title="${lint.generated_at}">${shortTs(lint.generated_at)}</span>` : '';
    lintEl.innerHTML = `${langText('Lint：', 'Lint:')} ${hs}<span class="${brokenCls}" title="${langText('指向不存在页面的 [[wikilink]]', 'Wikilinks pointing to nonexistent pages')}">${broken} ${langText('断链', 'broken links')}</span> · <span class="${staleCls}" title="${langText('缺少 last_compiled_at 元数据的页面', 'Pages missing last_compiled_at metadata')}">${stale} ${langText('缺元数据', 'no metadata')}</span> · <span class="${orphanCls}" title="${langText('没有任何入链引用的页面', 'Pages with zero inbound references')}">${orphan} ${langText('孤页', 'orphans')}</span> ${ts}`;
  }
}

function renderExecutionBrief(wiki) {
  const eb = wiki.execution_brief || {};
  const el = $('wiki-brief-content');
  if (!el) return;
  const compiledAt = eb.compiled_at ? shortTs(eb.compiled_at) : '—';
  const validUntil = eb.valid_until || '';
  let countdown = '';
  if (validUntil) {
    try {
      const diff = (new Date(validUntil) - Date.now()) / (1000 * 60 * 60 * 24);
      countdown = diff > 0 ? langText(`（剩余 ${Math.ceil(diff)} 天）`, `(${Math.ceil(diff)}d left)`) : langText('（已过期）', '(expired)');
    } catch (_) {}
  }
  const cs = eb.credit_strategy || {};
  const tokens = (cs.recommended_tokens || []).join(', ') || '—';
  const themes = eb.top_themes || [];
  const themesHtml = themes.map(th => {
    const pct = Math.min(100, Math.max(0, (th.heat_score ?? 0) * 100));
    return `<div class="wiki-theme-row"><span class="small" style="min-width:100px">${escHtml(th.name)}</span><div class="wiki-theme-bar"><div class="wiki-theme-bar-fill" style="width:${pct.toFixed(0)}%"></div></div><span class="mono small muted">${(th.heat_score ?? 0).toFixed(3)}</span></div>`;
  }).join('');

  el.innerHTML = `
    <div class="wiki-dir-row"><span class="wiki-dir-name">compiled_at</span><span class="mono small">${escHtml(compiledAt)}</span></div>
    <div class="wiki-dir-row"><span class="wiki-dir-name">valid_until</span><span class="mono small">${validUntil ? escHtml(shortTs(validUntil)) : '—'} <span class="muted">${escHtml(countdown)}</span></span></div>
    <div class="wiki-dir-row"><span class="wiki-dir-name">tokens</span><span class="mono small">${escHtml(tokens)}</span></div>
    ${themesHtml || '<div class="muted small">no themes</div>'}`;
}

function renderIngestMatrix(wiki) {
  const pipes = wiki.ingest_pipeline || [];
  const tbody = $('wiki-ingest-tbody');
  const summaryEl = $('wiki-ingest-summary');
  if (!tbody) return;
  const okCount = pipes.filter(p => p.status === 'ok').length;
  if (summaryEl) summaryEl.textContent = langText(`${okCount}/${pipes.length} 正常`, `${okCount}/${pipes.length} ok`);
  tbody.innerHTML = pipes.map(p => {
    const st = (p.status || 'missing').toLowerCase();
    const badgeCls = st === 'ok' ? 'ok' : st === 'stale' ? 'stale' : 'missing';
    const lastRun = p.last_run ? shortTs(p.last_run) : '—';
    const flow = (p.raw_output && p.raw_output !== '—' ? p.raw_output : '') + (p.wiki_output ? ' → ' + p.wiki_output : '');
    const findingsBadge = p.has_findings ? ` <span class="wiki-badge stale" title="${langText('有 lint 发现需要关注', 'Lint found issues')}">${langText('有发现', 'findings')}</span>` : '';
    return `<tr><td class="mono small">${escHtml(p.name)}</td><td class="muted small">${escHtml(p.script)}</td><td class="muted small">${escHtml(p.freq)}</td><td class="muted small" style="font-size:.7em">${flow || '—'}</td><td class="mono small">${escHtml(lastRun)}</td><td><span class="wiki-badge ${badgeCls}">${escHtml(st)}</span>${findingsBadge}</td></tr>`;
  }).join('');
}

function renderContractHealth(wiki) {
  const el = $('wiki-contract-health');
  if (!el) return;
  const ch = wiki.contract_health;
  if (!ch || ch.status === 'unknown') {
    el.innerHTML = '<span class="muted small">' + langText('合约验证数据不可用', 'contract verifier data unavailable') + '</span>';
    return;
  }
  const statusCls = ch.status === 'ok' ? 'clr-ok' : 'clr-error';
  const statusLabel = ch.status === 'ok' ? langText('通过', 'OK') : langText('降级', 'DEGRADED');
  const verifiedAt = ch.verified_at ? shortTs(ch.verified_at) : '—';
  const ageText = ch.age_hours != null ? formatAgeText(ch.age_hours) : '—';
  let failHtml = '';
  if (ch.top_failures && ch.top_failures.length > 0) {
    failHtml = '<div style="margin-top:4px">' + ch.top_failures.map(f =>
      '<div class="small clr-error" style="padding-left:8px">' + escHtml(f) + '</div>'
    ).join('') + '</div>';
  }
  el.innerHTML =
    '<div class="wiki-dir-row">' +
      '<span class="wiki-dir-name">' + langText('状态', 'status') + '</span>' +
      '<span class="mono small ' + statusCls + '">' + escHtml(statusLabel) + '</span>' +
    '</div>' +
    '<div class="wiki-dir-row">' +
      '<span class="wiki-dir-name">' + langText('通过/失败', 'pass/fail') + '</span>' +
      '<span class="mono small"><span class="clr-ok">' + (ch.pass || 0) + '</span> / <span class="' + (ch.fail > 0 ? 'clr-error' : 'clr-ok') + '">' + (ch.fail || 0) + '</span></span>' +
    '</div>' +
    '<div class="wiki-dir-row">' +
      '<span class="wiki-dir-name">' + langText('验证时间', 'verified_at') + '</span>' +
      '<span class="mono small">' + escHtml(verifiedAt) + ' <span class="muted">(' + escHtml(ageText) + ')</span></span>' +
    '</div>' +
    failHtml +
    (ch.alert_severity && ch.alert_severity !== 'unknown' && ch.alert_severity !== 'clear' ?
      '<div class="wiki-dir-row" style="margin-top:4px">' +
        '<span class="wiki-dir-name">' + langText('告警', 'alert') + '</span>' +
        '<span class="mono small clr-error">' + escHtml(ch.alert_severity.toUpperCase()) +
        (ch.alert_message ? ' — ' + escHtml(ch.alert_message) : '') +
        '</span></div>' : '');
}

function renderAgentWikiStatus(wiki) {
  const agentEl = $('wiki-agent-cards');
  if (!agentEl) return;
  const agents = wiki.agent_wiki_status || {};
  const icons = { main: '🧠', bookmarker: '📌', trader: '💰' };
  const agentKeys = {
    main: ['wiki_brief_available', 'wiki_top_theme', 'wiki_content_direction', 'wiki_trending_ticks', 'wiki_platform_available'],
    bookmarker: ['wiki_brief_available', 'wiki_top_theme', 'wiki_trending_ticks', 'wiki_platform_available'],
    trader: ['wiki_platform_available', 'wiki_trending_ticks', 'wiki_credit_vp_threshold', 'wiki_brief_available'],
  };
  agentEl.innerHTML = ['main', 'bookmarker', 'trader'].map(name => {
    const d = agents[name] || {};
    const keys = agentKeys[name] || Object.keys(d);
    const rows = keys.map(k => {
      let v = d[k];
      if (Array.isArray(v)) {
        v = v.map(t => `<span class="wiki-tick-pill">${escHtml(t)}</span>`).join('');
        return `<div class="kv-row"><span class="k">${escHtml(k.replace('wiki_', ''))}</span><span class="v">${v}</span></div>`;
      }
      const display = v === true ? '<span class="clr-ok">true</span>' : v === false ? '<span class="clr-error">false</span>' : escHtml(String(v ?? '—'));
      return `<div class="kv-row"><span class="k">${escHtml(k.replace('wiki_', ''))}</span><span class="v">${display}</span></div>`;
    }).join('');
    return `<div class="wiki-agent-card"><div class="agent-name"><span>${icons[name] || ''}</span> ${escHtml(name)}</div>${rows || '<span class="muted small">no wiki fields</span>'}</div>`;
  }).join('');
}

// ── Community Heat Map (visual) ──
function renderCommunityHeatMap(wiki) {
  const el = $('community-heat-map');
  if (!el) return;
  const ch = wiki.community_heat;
  if (!ch || !ch.ticks || !Object.keys(ch.ticks).length) {
    el.innerHTML = '<span class="muted small">— unavailable</span>';
    return;
  }
  // Sort ticks by heat_rank (or composite_score desc)
  const tickEntries = Object.entries(ch.ticks).sort((a, b) => {
    const ra = a[1].heat_rank != null ? a[1].heat_rank : 999;
    const rb = b[1].heat_rank != null ? b[1].heat_rank : 999;
    return ra - rb;
  });
  let html = '';
  if (ch.source_health && ch.source_health !== 'ok') {
    html += `<div class="muted small clr-warn" style="margin-bottom:4px">⚠ ${langText('热度数据陈旧', 'heat data stale')}</div>`;
  }
  if (ch.version) {
    html += `<div class="muted small" style="margin-bottom:4px;opacity:0.5">${escHtml(ch.version)}</div>`;
  }
  for (const [tick, v] of tickEntries) {
    const trend = v.trend || 'stable';
    const arrow = trend === 'rising' ? '↑' : trend === 'declining' ? '↓' : '→';
    const composite = v.composite_score != null ? Number(v.composite_score) : (v.trend_score != null ? Number(v.trend_score) : 0);
    const socialS = v.social_score != null ? Number(v.social_score) : 0;
    const tradeS = v.trade_score != null ? Number(v.trade_score) : 0;
    const opacity = Math.min(1, Math.max(0.15, composite));
    const bg = trend === 'rising' ? `rgba(0,210,106,${opacity})` : trend === 'declining' ? `rgba(255,77,77,${opacity})` : `rgba(139,148,158,${opacity})`;
    const rank = v.heat_rank != null ? '#' + v.heat_rank : '';
    const momS = v.social_momentum || '';
    const momT = v.trade_momentum || '';
    html += `<div class="heat-cell" style="background:${bg}">
      <div class="heat-cell-tick">${escHtml(tick)} ${escHtml(rank)}</div>
      <div class="heat-cell-trend">${arrow} ${composite.toFixed(2)}</div>
      <div class="heat-cell-meta" style="font-size:0.7em;opacity:0.85">S:${socialS.toFixed(2)} T:${tradeS.toFixed(2)}</div>
      <div class="heat-cell-meta" style="font-size:0.65em;opacity:0.6">${escHtml(momS)}/${escHtml(momT)}</div>
    </div>`;
  }
  el.innerHTML = html;
}

function renderPobUnclaimed(wiki) {
  const bigEl = $('pob-unclaimed-big');
  const barEl = $('pob-unclaimed-bar');
  if (!bigEl) return;
  const usd = wiki.pob_unclaimed_usd;
  const norm = wiki.pob_norm;
  if (usd == null) {
    bigEl.innerHTML = `<span class="muted">${langText('—（不可用）', '— (unavailable)')}</span>`;
    if (barEl) barEl.innerHTML = '';
    return;
  }
  bigEl.innerHTML = '<span class="mono pob-value">$' + Number(usd).toFixed(2) + '</span>';
  if (barEl && norm != null) {
    const pct = Math.min(100, Math.max(0, (usd / 2) * 100));
    const colorCls = pct >= 100 ? 'pob-bar-green' : pct >= 50 ? 'pob-bar-yellow' : 'pob-bar-gray';
    barEl.innerHTML = `<div class="pob-bar-fill ${colorCls}" style="width:${pct.toFixed(1)}%"></div>`;
  }
}

// ══════════════════════════════════════════════════════════════════════════
// Section 5: Timeline
// ══════════════════════════════════════════════════════════════════════════
function renderTimeline(data) {
  const items = data.items || [];
  const listEl = $('timeline-list');
  const countEl = $('timeline-count');
  if (countEl) countEl.textContent = items.length ? `(${items.length})` : '';
  if (!listEl) return;

  if (!items.length) {
    listEl.innerHTML = `<div class="muted small">${t('no-timeline')}</div>`;
    return;
  }

  const actorColors = {
    main: 'src-main',
    bookmarker: 'src-bookmarker',
    trader: 'src-trader',
    social: 'src-social',
  };

  listEl.innerHTML = items.map(it => {
    const srcCls = actorColors[it.source] || 'src-trader';
    const stCls = statusClass(it.status);
    const actorLabel = humanize(it.actor || it.source);
    const actionLabel = humanize(it.action || it.type);
    const noteLabel = cleanSummary(it.summary || it.note || '');
    return `<div class="tl-item">
      <span class="tl-ts">${shortTs(it.ts)}</span>
      <span class="tl-src ${srcCls}">${escHtml(actorLabel)}</span>
      <span class="tl-type ${stCls ? 'clr-' + stCls : ''}">${escHtml(actionLabel)}</span>
      <span class="tl-note">${escHtml(noteLabel)}</span>
    </div>`;
  }).join('');
}

// ══════════════════════════════════════════════════════════════════════════
// V2: Operator Language Formatter
// ══════════════════════════════════════════════════════════════════════════
const OPERATOR_LANG = {
  discard_previous_strategy: { zh: '切换策略', en: 'Switch Strategy' },
  reinforce_previous_strategy: { zh: '强化当前策略', en: 'Reinforce Strategy' },
  conservative_explore: { zh: '保守探索', en: 'Conservative Explore' },
  high_activity_only: { zh: '仅选择高活跃目标', en: 'High-activity only' },
  any: { zh: '任意目标', en: 'Any target' },
  community_aligned: { zh: '优先社区对齐目标', en: 'Community-aligned' },
  post_sync: { zh: '同步后再发帖', en: 'Post after sync' },
  none: { zh: '不主动互动', en: 'No extra engagement' },
  switch_engagement: { zh: '切换互动策略', en: 'Switch engagement' },
  reinforce: { zh: '继续强化', en: 'Reinforce' },
  regressing: { zh: 'TAS 回落', en: 'Regressing' },
  holding: { zh: '维持观望', en: 'Holding' },
  discard: { zh: '放弃这组策略', en: 'Discard' },
  reply_to_top_agents: { zh: '优先回复高价值账号', en: 'Reply to top agents' },
  // Claude Dispatch modes
  running: { zh: '运行中', en: 'Running' },
  idle: { zh: '空闲', en: 'Idle' },
  blocked: { zh: '阻塞', en: 'Blocked' },
  standby: { zh: '待命', en: 'Standby' },
  // Agent modes
  active: { zh: '活跃', en: 'Active' },
  conservative: { zh: '保守', en: 'Conservative' },
  stale: { zh: '陈旧', en: 'Stale' },
  // Dev next actions
  '等待执行完成': { zh: '等待执行完成', en: 'Waiting for execution' },
  '等待 main 派单': { zh: '等待 main 派单', en: 'Awaiting dispatch from main' },
  '修复 blocker / 重新 dispatch': { zh: '修复阻塞 / 重新派单', en: 'Fix blocker / re-dispatch' },
  // Common
  hold: { zh: '暂停', en: 'Hold' },
  maintain: { zh: '维持', en: 'Maintain' },
  'maintain current strategy': { zh: '维持当前策略', en: 'Maintain current strategy' },
  'rewrite social-intent / treasury-policy': { zh: '重写社交意图/国库策略', en: 'Rewrite social-intent / treasury-policy' },
  'repair social freshness': { zh: '修复社交数据时效', en: 'Repair social freshness' },
  'execute social intent': { zh: '执行社交意图', en: 'Execute social intent' },
  'recover topic pipeline': { zh: '恢复话题链路', en: 'Recover topic pipeline' },
  'repair source health': { zh: '修复数据源健康', en: 'Repair source health' },
};

function operatorLang(key) {
  return humanize(key);
}

// ══════════════════════════════════════════════════════════════════════════
// Human-friendly term dictionary + humanize()
// ══════════════════════════════════════════════════════════════════════════
const HUMAN_TERMS = {
  // 策略模式
  conservative_explore:          { zh: '保守探索中', en: 'Exploring cautiously' },
  reinforce_previous_strategy:   { zh: '延续当前策略', en: 'Staying the course' },
  discard_previous_strategy:     { zh: '切换新策略', en: 'Switching strategy' },
  // 运行模式
  'vp-flush':                    { zh: '消耗投票权模式', en: 'Using voting power' },
  standard:                      { zh: '正常自主', en: 'Normal operation' },
  aggressive:                    { zh: '激进模式', en: 'Aggressive mode' },
  conservative:                  { zh: '保守模式', en: 'Conservative' },
  // 资源
  OP:                            { zh: '操作权力 (点)', en: 'Operation Power (Point)' },
  VP:                            { zh: '投票权', en: 'Voting Power' },
  // 数据时效
  fresh:                         { zh: '实时', en: 'Fresh' },
  aging:                         { zh: '偏旧', en: 'Aging' },
  stale:                         { zh: '过时', en: 'Stale' },
  critical:                      { zh: '严重过时', en: 'Critical' },
  rising:                        { zh: '上升', en: 'Rising' },
  declining:                     { zh: '下降', en: 'Declining' },
  ok:                            { zh: '正常', en: 'OK' },
  partial:                       { zh: '部分', en: 'Partial' },
  missing:                       { zh: '缺失', en: 'Missing' },
  unavailable:                   { zh: '不可用', en: 'Unavailable' },
  inactive:                      { zh: '未激活', en: 'Inactive' },
  on:                            { zh: '开启', en: 'On' },
  off:                           { zh: '关闭', en: 'Off' },
  compiled:                      { zh: '已编译', en: 'Compiled' },
  true:                          { zh: '是', en: 'true' },
  false:                         { zh: '否', en: 'false' },
  broken:                        { zh: '损坏链接', en: 'Broken' },
  orphan:                        { zh: '孤儿项', en: 'Orphan' },
  // 决策
  authorize:                     { zh: '已授权', en: 'Authorized' },
  deny:                          { zh: '已拒绝', en: 'Denied' },
  allow:                         { zh: '允许', en: 'Allowed' },
  // 系统状态
  normal:                        { zh: '运行正常', en: 'Running normally' },
  degraded:                      { zh: '降级运行', en: 'Degraded' },
  repair:                        { zh: '修复中', en: 'Repairing' },
  // Agent 名称
  main:                          { zh: '策略总控', en: 'Strategy Agent' },
  bookmarker:                    { zh: '社交执行', en: 'Social Agent' },
  trader:                        { zh: '交易管理', en: 'Trading Agent' },
  claude_dispatch:               { zh: '开发助手', en: 'Dev Assistant' },
};

function humanize(key, lang) {
  if (!key || key === '—') return key || '—';
  const l = lang || _lang || 'zh';
  // Check HUMAN_TERMS first, then fall back to OPERATOR_LANG
  const htEntry = HUMAN_TERMS[key];
  if (htEntry) return htEntry[l] || htEntry['zh'] || key;
  const olEntry = OPERATOR_LANG[key];
  if (olEntry) return olEntry[l] || olEntry['en'] || key;
  return key;
}

// ══════════════════════════════════════════════════════════════════════════
// Hero Summary Bar + Action Required Strip
// ══════════════════════════════════════════════════════════════════════════

function renderHeroBar(controlTower, timeline, agentHealth, status) {
  try {
    // ── Status dot ──
    const mode = (controlTower && controlTower.system_mode) || 'normal';
    const dotEl   = $('heroStatusDot');
    const labelEl = $('heroStatusLabel');
    if (dotEl)   dotEl.textContent   = mode === 'normal' ? '🟢' : mode === 'degraded' ? '🟡' : '🔴';
    if (labelEl) labelEl.textContent = humanize(mode);

    // ── TAS score ──
    const tasScoreEl = $('heroTasScore');
    const tasTrendEl = $('heroTasTrend');
    let tasVal = null;
    try {
      const st = status && (status.tas || status);
      if (st && st.tas_total != null) tasVal = parseFloat(st.tas_total);
    } catch (_) {}
    if (tasScoreEl) tasScoreEl.textContent = tasVal != null ? tasVal.toFixed(2) : '—';
    if (tasTrendEl) {
      // Compare to previous cached value if available
      let prevTas = null;
      try {
        const history = (_lastStatus && _lastStatus.tas_history) || [];
        if (history.length >= 2) prevTas = parseFloat(history[history.length - 2].tas_total);
      } catch (_) {}
      tasTrendEl.textContent = tasVal == null ? '→'
        : prevTas == null   ? '→'
        : tasVal > prevTas  ? '↑'
        : tasVal < prevTas  ? '↓'
        : '→';
      tasTrendEl.className = 'hero-trend '
        + (tasVal == null || prevTas == null ? 'flat'
          : tasVal > prevTas ? 'up'
          : tasVal < prevTas ? 'down'
          : 'flat');
    }

    // ── Today summary ──
    const todayEl = $('heroToday');
    if (todayEl) {
      const summ    = timeline && timeline.summary;
      const posts    = summ && summ.posts_24h    != null ? summ.posts_24h    : null;
      const curations = summ && summ.curations_24h != null ? summ.curations_24h : null;
      let portfolioUsd = null, claimableUsd = null;
      try {
        const tr = agentHealth && agentHealth.agents && agentHealth.agents.trader;
        if (tr) {
          if (tr.portfolio_usd_raw != null) portfolioUsd = parseFloat(tr.portfolio_usd_raw);
          if (tr.claimable_usd_raw != null) claimableUsd = parseFloat(tr.claimable_usd_raw);
          // fall back to onchain sub-keys
          if (portfolioUsd == null && tr.onchain && tr.onchain.total_portfolio_usd != null)
            portfolioUsd = parseFloat(tr.onchain.total_portfolio_usd);
        }
      } catch (_) {}
      const zh = _lang === 'zh';
      const parts = [
        posts      != null ? (zh ? `发帖 ${posts} 篇`       : `Posts ${posts}`)         : null,
        curations  != null ? (zh ? `策展 ${curations} 条`   : `Curations ${curations}`) : null,
        portfolioUsd != null ? (zh ? `持仓 $${portfolioUsd.toFixed(2)}` : `Portfolio $${portfolioUsd.toFixed(2)}`) : null,
        claimableUsd != null ? (zh ? `奖励 $${claimableUsd.toFixed(2)}` : `Rewards $${claimableUsd.toFixed(2)}`)  : null,
      ].filter(Boolean);
      const prefix = zh ? '今天：' : 'Today: ';
      todayEl.textContent = prefix + (parts.length ? parts.join(' · ') : '—');
    }
  } catch (e) { console.warn('[heroBar]', e); }
}

function renderActionRequired(controlTower, agentHealth) {
  const strip = $('action-required-strip');
  if (!strip) return;
  const items = [];

  // Alerts from control tower
  try {
    const alerts = (controlTower && controlTower.alerts) || [];
    alerts.forEach(a => {
      const sev = a.severity || a.level || '';
      if (sev === 'critical' || sev === 'warning') {
        items.push({ level: sev, msg: humanize(a.message || a.msg || '') });
      }
    });
  } catch (_) {}

  // Blockers from agent health
  try {
    const agents = (agentHealth && agentHealth.agents) || {};
    Object.entries(agents).forEach(([id, agent]) => {
      if (!agent) return;
      const b = agent.blocker;
      if (b && b !== '—' && b !== 'none' && b !== '' && b !== null) {
        items.push({ level: 'warning', msg: `${humanize(id)}：${humanize(b)}` });
      }
    });
  } catch (_) {}

  if (!items.length) {
    strip.style.display = 'none';
    return;
  }
  strip.style.display = 'flex';
  strip.innerHTML = items.map(it => {
    const cls = it.level === 'critical' ? 'ar-pill ar-critical' : 'ar-pill ar-warning';
    return `<span class="${cls}">⚠️ ${escHtml(it.msg)}</span>`;
  }).join('');
}

// ══════════════════════════════════════════════════════════════════════════
// V2: Control Tower Render
// ══════════════════════════════════════════════════════════════════════════
function renderControlTower(data) {
  if (!data) return;

  // Mission Status Bar slots removed (SYSTEM MODE, PRIMARY BOTTLENECK, HIGHEST PRIORITY, TAS LEVER, CONFIDENCE)

  // Alerts Strip
  const alertsEl = $('alerts-strip');
  if (alertsEl) {
    const alerts = data.alerts || [];
    if (!alerts.length) {
      alertsEl.innerHTML = `<span class="muted small">${t('no-blockers')}</span>`;
    } else {
      alertsEl.innerHTML = alerts.slice(0, 5).map(a => {
        const cls = a.level === 'critical' ? 'alert-critical' : a.level === 'warning' ? 'alert-warning' : 'alert-info';
        return `<span class="alert-pill ${cls}">${escHtml(a.message)}</span>`;
      }).join('');
    }
  }

  // Add meta badges to TAS CC columns
  const freshness = data.freshness || {};
  _addMetaBadge('tas-col-bookmarker', freshness.tas_social);
  _addMetaBadge('tas-col-center', freshness.tas_latest);
  _addMetaBadge('tas-col-trader', freshness.tas_trade);
}

function _addMetaBadge(parentId, info) {
  const parent = $(parentId);
  if (!parent || !info) return;
  // Remove existing meta badge
  const existing = parent.querySelector('.meta-badge');
  if (existing) existing.remove();
  const bucket = info.bucket || 'na';
  const cls = 'meta-' + bucket;
  const badge = document.createElement('div');
  badge.className = 'meta-badge';
  badge.innerHTML = `<span class="meta-badge-item ${cls}">${escHtml(bucket)}</span>`;
  parent.appendChild(badge);
}

// ══════════════════════════════════════════════════════════════════════════
// V2: Agent Health Render
// ══════════════════════════════════════════════════════════════════════════
function renderAgentHealth(data) {
  if (!data) return;
  // Operator Lanes section removed from dashboard
  renderAgentOperatingCards(data.agents || {});
  renderFreshnessMatrix(data.freshness_matrix || []);
}

function renderOperatorLanes(data) {
  // Observe
  const obsEl = $('lane-observe-body');
  if (obsEl) {
    const o = data.observe || {};
    obsEl.innerHTML = [
      _laneKV('TAS Total', fmt(o.tas_total)),
      _laneKV('TAS Social', fmt(o.tas_social)),
      _laneKV('TAS Trade', fmt(o.tas_trade)),
      _laneKV('Heat Health', o.community_heat_health || '—'),
      _laneKV('Source', o.source_health_status || '—'),
    ].join('');
  }

  // Decide
  const decEl = $('lane-decide-body');
  if (decEl) {
    const d = data.decide || {};
    decEl.innerHTML = [
      _laneKV('Strategy', operatorLang(d.strategy_action)),
      _laneKV('Social', d.social_decision || '—'),
      _laneKV('Treasury', d.treasury_decision || '—'),
      _laneKV('Focus', (d.planning_focus || '—').slice(0, 50)),
      _laneKV('Autonomy', d.autonomy_mode || '—'),
    ].join('');
  }

  // Execute
  const exeEl = $('lane-execute-body');
  if (exeEl) {
    const e = data.execute || {};
    const authIcon = e.social_intent_authorized ? '✓' : '✗';
    const authCls = e.social_intent_authorized ? 'clr-ok' : 'clr-warn';
    exeEl.innerHTML = [
      `<div class="lane-kv"><span class="lk">Intent Auth</span><span class="lv ${authCls}">${authIcon}</span></div>`,
      _laneKV('Recent Actions', e.social_actions_recent || 0),
      _laneKV('Trader Rec', (e.trader_recommended || []).join(', ') || 'hold'),
      _laneKV('Claimable', e.reward_claimable_usd != null ? '$' + fmt(e.reward_claimable_usd) : '—'),
    ].join('');
  }
}

function _laneKV(label, value) {
  return `<div class="lane-kv"><span class="lk">${escHtml(label)}</span><span class="lv">${escHtml(String(value))}</span></div>`;
}

function renderAgentOperatingCards(agents) {
  for (const agentId of ['main', 'bookmarker', 'trader', 'claude_dispatch']) {
    const el = $('agent-card-' + agentId);
    if (!el) continue;
    const a = agents[agentId];
    if (!a) { el.innerHTML = ''; continue; }
    const freshCls = 'meta-' + (a.freshness || 'na');
    const blockerCls = a.blocker ? 'aoc-blocker' : 'aoc-no-blocker';
    const roleDisplay = agentId === 'claude_dispatch' ? langText('开发执行器', 'Dispatch Executor') : (translateLiteral(a.role || '') || a.role || humanize(agentId));
    const slots = [];
    // For bookmarker, show OP/VP first in the same row as Role/Mode/etc.
    if (agentId === 'bookmarker' && (a.op != null || a.vp != null)) {
      const opVal = a.op != null ? Number(a.op).toFixed(1) : '—';
      const vpVal = a.vp != null ? Number(a.vp).toFixed(1) : '—';
      slots.push(`<div class="aoc-slot"><span class="aoc-label">${t('OP')}</span><span class="aoc-value">${escHtml(opVal)}</span></div>`);
      slots.push(`<div class="aoc-slot"><span class="aoc-label">${t('VP')}</span><span class="aoc-value">${escHtml(vpVal)}</span></div>`);
      el.classList.add('aoc-7col');
    } else {
      el.classList.remove('aoc-7col');
    }
    slots.push(
      `<div class="aoc-slot"><span class="aoc-label">${t('aoc-role')}</span><span class="aoc-value">${escHtml(roleDisplay)}</span></div>`,
      `<div class="aoc-slot"><span class="aoc-label">${t('aoc-mode')}</span><span class="aoc-value">${escHtml(operatorLang(a.mode))}</span></div>`,
      `<div class="aoc-slot"><span class="aoc-label">${t('aoc-freshness')}</span><span class="aoc-value ${freshCls}">${escHtml(a.freshness || '—')}</span></div>`,
      `<div class="aoc-slot"><span class="aoc-label">${t('aoc-blocker')}</span><span class="aoc-value ${blockerCls}">${escHtml(a.blocker || 'none')}</span></div>`,
      `<div class="aoc-slot"><span class="aoc-label">${t('aoc-next-action')}</span><span class="aoc-value">${escHtml(operatorLang(a.next_action))}</span></div>`,
    );
    el.innerHTML = slots.join('');
  }
}

function renderFreshnessMatrix(matrix) {
  const el = $('freshness-matrix');
  if (!el || !matrix.length) return;
  const cols = ['tas', 'intent', 'pipeline', 'wallet', 'wiki', 'skills'];
  const agentLabels = {
    main: _lang === 'zh' ? '主控' : 'main',
    bookmarker: _lang === 'zh' ? '书签员' : 'bookmarker',
    trader: _lang === 'zh' ? '交易员' : 'trader',
    claude_dispatch: _lang === 'zh' ? '鲁班' : 'dispatch',
  };
  let html = '<div class="freshness-matrix">';
  // Header row
  html += '<div class="fm-header"></div>';
  cols.forEach(c => { html += `<div class="fm-header">${escHtml(c)}</div>`; });
  // Data rows
  matrix.forEach(row => {
    const label = agentLabels[row.agent] || row.agent;
    html += `<div class="fm-agent-label">${escHtml(label)}</div>`;
    cols.forEach(c => {
      const val = row[c] || 'na';
      html += `<div class="fm-cell fm-${val}">${escHtml(val)}</div>`;
    });
  });
  html += '</div>';
  el.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════════════════
// V2: Timeline Summary
// ══════════════════════════════════════════════════════════════════════════
function renderTimelineSummary(summary) {
  const el = $('timeline-summary-row');
  if (!el || !summary) return;
  el.innerHTML = [
    _tlPill(t('tl-posts'), summary.posts_24h),
    _tlPill(t('tl-curations'), summary.curations_24h),
    _tlPill(t('tl-claims'), summary.claims_24h),
    _tlPill(t('tl-blocked'), summary.blocked_24h),
    _tlPill(t('tl-dominant'), summary.dominant_agent || 'none'),
    _tlPill(t('tl-last-ok'), summary.last_success_at ? shortTs(summary.last_success_at) : '—'),
  ].join('');
}

function _tlPill(label, value) {
  return `<div class="tl-summary-pill"><span class="tsp-label">${escHtml(label)}</span><span class="tsp-value">${escHtml(String(value ?? 0))}</span></div>`;
}

// ══════════════════════════════════════════════════════════════════════════
// NOC / Intelligence Renderers
// ══════════════════════════════════════════════════════════════════════════

function renderNoc(data) {
  if (!data) return;
  renderDependencyGraph(data.dependency_graph);
  renderStateMachines(data.state_machines);
  renderCountdowns(data.countdowns);
  renderCommunityHeatVisual(data.intelligence);
  renderIntelligenceSummary(data.intelligence);
}

function renderDependencyGraph(dg) {
  const el = $('dependency-graph');
  if (!el || !dg) { if (el) el.innerHTML = '<div class="muted small">— unavailable</div>'; return; }

  const nodes = dg.nodes || [];
  const edges = dg.edges || [];
  const nodeMap = {};
  nodes.forEach(n => { nodeMap[n.id] = n; });
  const edgeMap = {};
  edges.forEach(e => { edgeMap[e.from + '->' + e.to] = e; });

  const chains = [
    { label: 'Social Pipeline', ids: ['x_sync', 'topic_brief', 'social_intent', 'social_actions'] },
    { label: 'Treasury Pipeline', ids: ['reward_status', 'tas_trade', 'treasury_policy', 'claim_trade'] },
    { label: 'Wiki Pipeline', ids: ['raw', 'wiki_compile', 'agent_read', 'decision'] },
  ];

  let html = '<div class="dep-graph-chains">';
  chains.forEach(chain => {
    html += `<div><div class="dep-chain-label">${escHtml(chain.label)}</div><div class="dep-chain">`;
    chain.ids.forEach((id, i) => {
      const node = nodeMap[id] || { label: id, status: 'missing' };
      const stCls = 'st-' + (node.status || 'missing');
      const statusBg = _depStatusBg(node.status);
      html += `<div class="dep-node ${stCls}"><div class="dep-node-label">${escHtml(node.label)}</div><div class="dep-node-status" style="background:${statusBg}">${escHtml(node.status || '?')}</div></div>`;
      if (i < chain.ids.length - 1) {
        const nextId = chain.ids[i + 1];
        const edge = edgeMap[id + '->' + nextId] || { status: 'ok' };
        const eCls = 'e-' + (edge.status || 'ok');
        html += `<div class="dep-edge ${eCls}">→</div>`;
      }
    });
    html += '</div></div>';
  });
  html += '</div>';
  el.innerHTML = html;
}

function _depStatusBg(status) {
  switch (status) {
    case 'fresh': return 'rgba(0,210,106,.15)';
    case 'aging': return 'rgba(240,165,0,.15)';
    case 'stale': return 'rgba(251,146,60,.18)';
    case 'critical': return 'rgba(255,77,77,.18)';
    default: return 'rgba(139,148,158,.08)';
  }
}

function renderStateMachines(sm) {
  const el = $('state-machines');
  if (!el || !sm) { if (el) el.innerHTML = '<div class="muted small">— unavailable</div>'; return; }

  const agents = ['main', 'bookmarker', 'trader'];
  const icons = { main: '🧠', bookmarker: '📌', trader: '💰' };

  let html = '<div class="sm-container">';
  agents.forEach(agent => {
    const machine = sm[agent];
    if (!machine) return;
    const steps = machine.steps || [];
    const current = machine.current_step;
    const currentIdx = steps.indexOf(current);

    html += `<div class="sm-row"><span class="sm-agent-label">${icons[agent] || ''} ${escHtml(agent)}</span><div class="sm-steps">`;
    steps.forEach((step, i) => {
      let cls = 'sm-step';
      if (i < currentIdx) cls += ' sm-done';
      else if (i === currentIdx) cls += ' sm-current';
      else cls += ' sm-pending';
      html += `<div class="${cls}">${escHtml(step)}</div>`;
      if (i < steps.length - 1) {
        const connCls = i < currentIdx ? 'sm-connector sm-done-conn' : 'sm-connector';
        html += `<div class="${connCls}"></div>`;
      }
    });
    html += '</div></div>';
  });
  html += '</div>';
  el.innerHTML = html;
}

function formatCountdownText(diffMin) {
  if (diffMin <= 0) {
    const ago = Math.abs(diffMin);
    return ago < 60
      ? langText(`${Math.round(ago)}分钟前`, `${Math.round(ago)}m ago`)
      : langText(`${Math.round(ago / 60)}小时前`, `${Math.round(ago / 60)}h ago`);
  }
  if (diffMin < 60) return langText(`${Math.round(diffMin)}分钟`, `${Math.round(diffMin)}m`);
  if (diffMin < 1440) return langText(`${Math.round(diffMin / 60)}小时`, `${Math.round(diffMin / 60)}h`);
  return langText(`${Math.round(diffMin / 1440)}天`, `${Math.round(diffMin / 1440)}d`);
}

function renderCountdowns(cd) {
  const el = $('countdown-strip');
  if (!el || !cd) { if (el) el.innerHTML = `<div class="muted small">${langText('—（不可用）', '— unavailable')}</div>`; return; }

  let html = '';

  // Heartbeat countdowns
  const hbItems = [
    { label: 'Main HB', data: cd.next_main_heartbeat_at },
    { label: 'Bookmarker HB', data: cd.next_bookmarker_heartbeat_at },
    { label: 'Trader HB', data: cd.next_trader_heartbeat_at },
  ];
  hbItems.forEach(item => {
    const d = item.data || {};
    html += _cdPill(item.label, d.next_at, d.estimated);
  });

  // Expiry countdowns
  html += _cdPill('Intent Expiry', cd.social_intent_expires_at, false);
  html += _cdPill('Treasury Expiry', cd.treasury_policy_expires_at, false);
  html += _cdPill('Wiki Brief Valid', cd.wiki_brief_valid_until, false);

  // Claim progress
  const claim = cd.claim_threshold_progress || {};
  const pct = Math.min(100, (claim.ratio || 0) * 100);
  const claimCls = pct >= 100 ? 'cd-healthy' : pct >= 50 ? 'cd-soon' : 'cd-expired';
  html += `<div class="cd-pill ${claimCls}">
    <span class="cd-pill-label">Claim Progress</span>
    <span class="cd-pill-value">$${(claim.current_usd || 0).toFixed(2)} / $${(claim.threshold_usd || 2).toFixed(2)}</span>
    <span class="cd-pill-label">${pct.toFixed(0)}%</span>
    <div class="cd-progress-wrap"><div class="cd-progress-fill" style="width:${pct.toFixed(1)}%;background:${pct >= 100 ? 'var(--ok)' : pct >= 50 ? 'var(--warn)' : 'var(--error)'}"></div></div>
  </div>`;

  el.innerHTML = html;
}

function _cdPill(label, ts, estimated) {
  if (!ts) return `<div class="cd-pill"><span class="cd-pill-label">${escHtml(translateLiteral(label))}</span><span class="cd-pill-value muted">—</span></div>`;
  try {
    const target = new Date(ts);
    const diff = target - Date.now();
    const diffMin = diff / 60000;
    const text = formatCountdownText(diffMin);
    const cls = diffMin <= 0 ? 'cd-expired' : diffMin < 60 ? 'cd-soon' : 'cd-healthy';
    const estTag = estimated ? `<span class="cd-pill-est">${langText('预计', 'est.')}</span>` : '';
    return `<div class="cd-pill ${cls}"><span class="cd-pill-label">${escHtml(translateLiteral(label))}</span><span class="cd-pill-value">${escHtml(text)}</span>${estTag}</div>`;
  } catch (_) {
    return `<div class="cd-pill"><span class="cd-pill-label">${escHtml(translateLiteral(label))}</span><span class="cd-pill-value muted">—</span></div>`;
  }
}

function miniHeatSparkSvg(values, color = '#58a6ff', w = 110, h = 26) {
  const pts = (values || []).map(v => Number(v)).filter(v => Number.isFinite(v));
  if (!pts.length) return '';
  const pad = 3;
  const chartW = w - pad * 2;
  const chartH = h - pad * 2;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = Math.max(max - min, 0.01);
  const toX = i => pad + (i / Math.max(pts.length - 1, 1)) * chartW;
  const toY = v => pad + chartH - ((v - min) / span) * chartH;
  const line = pts.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ');
  const dots = pts.map((v, i) => `<circle cx="${toX(i).toFixed(1)}" cy="${toY(v).toFixed(1)}" r="1.8" fill="${color}" opacity="0.9"/>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" class="heat-mini-spark" aria-hidden="true">
    <polyline fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" points="${line}"/>
    ${dots}
  </svg>`;
}

function renderCommunityHeatVisual(intel) {
  const el = $('community-heat-visual');
  if (!el || !intel) { if (el) el.innerHTML = `<div class="muted small">${langText('—（不可用）', '— unavailable')}</div>`; return; }
  const items = intel.community_heat_visual || [];
  if (!items.length) { el.innerHTML = `<div class="muted small">${langText('— 无热度数据', '— no heat data')}</div>`; return; }

  const formula = intel.community_heat_formula || {};
  const windows = intel.community_heat_windows || {};
  const formulaTip = [
    formula.composite ? `Composite: ${formula.composite}` : null,
    formula.temporal_blend ? `Blend: ${formula.temporal_blend}` : null,
    windows.burst_hours ? `Burst window: ${windows.burst_hours}h` : null,
    windows.sustained_hours ? `Sustained window: ${windows.sustained_hours}h` : null,
    formula.social_signal ? `Social: ${formula.social_signal}` : null,
    formula.trade_signal ? `Trade: ${formula.trade_signal}` : null,
  ].filter(Boolean).join('\n');

  let html = `<div class="heat-header-row">
    <div class="heat-header-title">${escHtml(langText('社区热度', 'Community Heat'))}</div>
    <div class="heat-header-help" title="${escHtml(formulaTip)}">ⓘ</div>
  </div>`;
  html += '<div class="heat-bars">';
  items.forEach(item => {
    const pct = Math.min(100, Math.max(5, (item.intensity || 0) * 100));
    const trend = item.trend || 'stable';
    const trendCls = 'hb-' + trend;
    const trendBadgeCls = 'hbt-' + trend;
    const arrow = trend === 'rising' ? '↑' : trend === 'declining' ? '↓' : '→';
    const rank = item.rank != null ? '#' + item.rank : '';
    const socialS = item.social_score != null ? item.social_score.toFixed(2) : '—';
    const tradeS = item.trade_score != null ? item.trade_score.toFixed(2) : '—';
    const cov = (item.data_coverage || []).join(', ') || 'full';
    const social7dDaily = item.social_engagement_7d != null ? (Number(item.social_engagement_7d) / 7).toFixed(2) : '—';
    const trade7dDaily = item.trade_volume_7d != null ? (Number(item.trade_volume_7d) / 7).toFixed(2) : '—';
    const sustained = Number(item.composite_sustained_score || 0);
    const current = Number(item.trend_score || 0);
    const burst = Number(item.composite_burst_score || 0);
    const delta = Number(item.composite_delta || 0);
    const socialDelta = Number(item.social_delta || 0);
    const tradeDelta = Number(item.trade_delta || 0);
    const rankDelta = Number(item.rank_delta || 0);
    const deltaCls = delta > 0.03 ? 'heat-delta-up' : delta < -0.03 ? 'heat-delta-down' : 'heat-delta-flat';
    const socialDeltaCls = socialDelta > 0.03 ? 'heat-delta-up' : socialDelta < -0.03 ? 'heat-delta-down' : 'heat-delta-flat';
    const tradeDeltaCls = tradeDelta > 0.03 ? 'heat-delta-up' : tradeDelta < -0.03 ? 'heat-delta-down' : 'heat-delta-flat';
    const rankDeltaCls = rankDelta > 0 ? 'heat-delta-up' : rankDelta < 0 ? 'heat-delta-down' : 'heat-delta-flat';
    const deltaText = `${delta >= 0 ? '+' : ''}${delta.toFixed(2)}`;
    const socialDeltaText = `${socialDelta >= 0 ? '+' : ''}${socialDelta.toFixed(2)}`;
    const tradeDeltaText = `${tradeDelta >= 0 ? '+' : ''}${tradeDelta.toFixed(2)}`;
    const rankDeltaText = rankDelta > 0 ? `↑ +${rankDelta}` : rankDelta < 0 ? `↓ ${rankDelta}` : '→ 0';
    const rankDeltaTitle = item.previous_rank != null ? `rank ${item.previous_rank} → ${item.rank}` : 'first snapshot or unchanged baseline';
    // Yesterday baseline
    const yesterdayRank = item.yesterday_rank;
    const yesterdayRankDelta = item.yesterday_rank_delta != null ? Number(item.yesterday_rank_delta) : null;
    const yesterdayRankDeltaCls = yesterdayRankDelta != null ? (yesterdayRankDelta > 0 ? 'heat-delta-up' : yesterdayRankDelta < 0 ? 'heat-delta-down' : 'heat-delta-flat') : 'heat-delta-flat';
    const yesterdayRankDeltaText = yesterdayRankDelta != null ? (yesterdayRankDelta > 0 ? `↑ +${yesterdayRankDelta}` : yesterdayRankDelta < 0 ? `↓ ${yesterdayRankDelta}` : '→ 0') : '—';
    const yesterdayRankTitle = yesterdayRank != null ? `vs yesterday: rank ${yesterdayRank} → ${item.rank}` : 'no yesterday baseline available';
    const sparkColor = delta > 0.03 ? '#00d26a' : delta < -0.03 ? '#ff6b6b' : '#58a6ff';
    const spark = miniHeatSparkSvg([sustained, current, burst], sparkColor);
    const isTopOne = item.rank === 1;
    html += `<details class="heat-card"${isTopOne ? ' open' : ''}>
      <summary class="heat-bar-row">
        <span class="heat-bar-tick">${escHtml(item.tick || '?')}</span>
        <div class="heat-bar-wrap"><div class="heat-bar-fill ${trendCls}" style="width:${pct.toFixed(0)}%"></div></div>
        <div class="heat-bar-meta">
          <span class="heat-bar-trend ${trendBadgeCls}">${arrow} ${escHtml(translateLiteral(trend))}</span>
          <span class="heat-bar-score">${(item.trend_score || 0).toFixed(2)}</span>
          <span class="heat-bar-rank">${escHtml(rank)}</span>
          <span class="heat-rank-delta ${rankDeltaCls}" title="${escHtml(rankDeltaTitle)}">${escHtml(rankDeltaText)}</span><span class="heat-rank-delta heat-rank-delta-yd ${yesterdayRankDeltaCls}" title="${escHtml(yesterdayRankTitle)}">${yesterdayRankDelta != null ? '24h ' + escHtml(yesterdayRankDeltaText) : '24h —'}</span>
          <span class="heat-bar-sub">S:${socialS} T:${tradeS}</span>
        </div>
      </summary>
      <div class="heat-card-detail">
        <div class="heat-detail-grid">
          <div class="heat-detail-col">
            <div class="heat-detail-title">${escHtml(langText('Social', 'Social'))}</div>
            <div class="heat-detail-row"><span>24h</span><span>${escHtml(String(item.social_posts_24h || 0))} posts · ${escHtml((item.social_engagement_24h || 0).toFixed ? (item.social_engagement_24h || 0).toFixed(1) : String(item.social_engagement_24h || 0))} eng</span></div>
            <div class="heat-detail-row"><span>7d</span><span>${escHtml(String(item.social_posts_7d || 0))} posts · ${escHtml((item.social_engagement_7d || 0).toFixed ? (item.social_engagement_7d || 0).toFixed(1) : String(item.social_engagement_7d || 0))} eng</span></div>
            <div class="heat-detail-row heat-detail-sub"><span>24h / 7d-day</span><span>${escHtml((item.social_engagement_24h || 0).toFixed ? (item.social_engagement_24h || 0).toFixed(1) : String(item.social_engagement_24h || 0))} / ${escHtml(social7dDaily)}</span></div>
            <div class="heat-detail-row heat-detail-sub"><span>momentum</span><span>${escHtml(item.social_momentum || '—')}</span></div>
          </div>
          <div class="heat-detail-col">
            <div class="heat-detail-title">${escHtml(langText('Trade', 'Trade'))}</div>
            <div class="heat-detail-row"><span>24h</span><span>${escHtml(String(item.trade_count_24h || 0))} trades · ${escHtml(String(item.trade_volume_24h || 0))} vol</span></div>
            <div class="heat-detail-row"><span>7d</span><span>${escHtml(String(item.trade_count_7d || 0))} trades · ${escHtml(String(item.trade_volume_7d || 0))} vol</span></div>
            <div class="heat-detail-row heat-detail-sub"><span>24h / 7d-day</span><span>${escHtml(String(item.trade_volume_24h || 0))} / ${escHtml(trade7dDaily)}</span></div>
            <div class="heat-detail-row heat-detail-sub"><span>momentum</span><span>${escHtml(item.trade_momentum || '—')}</span></div>
          </div>
        </div>
        <div class="heat-mini-trend-wrap">
          <div class="heat-mini-trend-head">
            <div class="heat-mini-trend-title">${escHtml(langText('热度变化', 'Heat delta'))}</div>
            <div class="heat-mini-delta ${deltaCls}" title="${escHtml(langText('Composite Δ: 24h burst score (' + burst.toFixed(3) + ') minus 7d sustained baseline (' + sustained.toFixed(3) + '). Positive = activity accelerating vs weekly norm.', 'Composite Δ: 24h burst score (' + burst.toFixed(3) + ') minus 7d sustained baseline (' + sustained.toFixed(3) + '). Positive = activity accelerating vs weekly norm.'))}">Δ ${escHtml(deltaText)}</div>
          </div>
          <div class="heat-mini-chart">${spark}</div>
          <div class="heat-mini-legend">
            <span>7d ${escHtml(sustained.toFixed(2))}</span>
            <span>now ${escHtml(current.toFixed(2))}</span>
            <span>24h ${escHtml(burst.toFixed(2))}</span>
          </div>
          <div class="heat-delta-row">
            <span class="heat-delta-pill ${socialDeltaCls}" title="${escHtml('SΔ = Social 24h burst (' + (item.social_burst_score || 0).toFixed(3) + ') − 7d sustained (' + (item.social_sustained_score || 0).toFixed(3) + '). Measures social engagement acceleration: posts, likes, replies, retweets in the last 24h vs weekly average.')}">SΔ ${escHtml(socialDeltaText)}</span>
            <span class="heat-delta-pill ${tradeDeltaCls}" title="${escHtml('TΔ = Trade 24h burst (' + (item.trade_burst_score || 0).toFixed(3) + ') − 7d sustained (' + (item.trade_sustained_score || 0).toFixed(3) + '). Measures onchain trade acceleration: transaction count and volume in the last 24h vs weekly average.')}">TΔ ${escHtml(tradeDeltaText)}</span>
            <span class="heat-delta-pill ${rankDeltaCls}" title="${escHtml(rankDeltaTitle + (yesterdayRank != null ? ' | vs yesterday: rank ' + yesterdayRank + ' → ' + item.rank : ' | no yesterday baseline'))}">RΔ ${escHtml(rankDeltaText)}</span>
          </div>
        </div>
        <div class="heat-explain-row">
          <span class="heat-explain-pill" title="${escHtml(formula.temporal_blend || '')}">24h + 7d blend</span>
          <span class="heat-explain-pill" title="${escHtml(formula.composite || '')}">composite ${(item.trend_score || 0).toFixed(2)}</span>
          <span class="heat-explain-pill" title="coverage">coverage: ${escHtml(cov)}</span>
        </div>
      </div>
    </details>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

function renderIntelligenceSummary(intel) {
  const el = $('intelligence-summary');
  if (!el || !intel) { if (el) el.innerHTML = `<div class="muted small">${langText('—（不可用）', '— unavailable')}</div>`; return; }

  let html = '<div class="intel-summary">';

  // Top themes
  const themes = intel.top_themes || [];
  html += `<div class="intel-row"><span class="intel-label">${escHtml(translateLiteral('Top Themes'))}</span><div class="intel-value">`;
  if (themes.length) {
    html += themes.map(th => `<span class="intel-theme-pill">${escHtml(translateLiteral(th.name || '?'))} ${(th.heat_score || 0).toFixed(3)}</span>`).join('');
  } else {
    html += `<span class="muted small">${langText('无', 'none')}</span>`;
  }
  html += '</div></div>';

  // Stale paths
  const stale = intel.stale_paths || [];
  html += `<div class="intel-row"><span class="intel-label">${escHtml(translateLiteral('Stale Paths'))}</span><div class="intel-value">`;
  if (stale.length) {
    html += stale.map(s => `<span class="intel-stale-pill">⚠ ${escHtml(s)}</span>`).join('');
  } else {
    html += `<span class="clr-ok small">${escHtml(translateLiteral('all fresh'))}</span>`;
  }
  html += '</div></div>';

  // Hottest signal
  html += `<div class="intel-row"><span class="intel-label">${escHtml(translateLiteral('Hottest'))}</span><div class="intel-value"><span class="intel-hottest">${escHtml(translateLiteral(intel.hottest_signal || '—'))}</span></div></div>`;

  html += '</div>';
  el.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════════════════
// P1: Next Post Preview Card
// ══════════════════════════════════════════════════════════════════════════
function renderNextPostPreview(status) {
  try {
    const card = $('next-post-preview');
    if (!card) return;
    // Try multiple data paths, silently degrade if none found
    const text = status?.social_pipeline?.post_directive?.text
      || status?.bookmarker?.social_pipeline?.post_directive?.text
      || status?.main?.social_intent?.payload?.post_directive?.text;
    if (!text) { card.style.display = 'none'; return; }
    card.style.display = 'block';

    // Topic tag from wiki_top_theme
    const topic = status?.bookmarker?.topic_brief?.wiki_top_theme
      || status?.main?.wiki_top_theme
      || '';
    const topicEl = $('nppTopic');
    if (topicEl) topicEl.textContent = topic ? `#${topic}` : '';

    // Body — collapse if > 150 chars
    const bodyEl = $('nppBody');
    if (bodyEl) {
      const isLong = text.length > 150;
      if (isLong) {
        bodyEl.innerHTML = `<span class="npp-text-short">${escHtml(text.slice(0, 150))}<span class="npp-ellipsis">… <button class="npp-expand-btn" onclick="nppToggle(this)">${translateLiteral('展开')}</button></span></span><span class="npp-text-full" style="display:none">${escHtml(text)}</span>`;
      } else {
        bodyEl.innerHTML = `<span class="npp-text-full">${escHtml(text)}</span>`;
      }
    }

    // Meta: char count
    const metaEl = $('nppMeta');
    if (metaEl) metaEl.textContent = formatCharCount(text.length);
  } catch (e) { console.warn('[renderNextPostPreview]', e); }
}

function nppToggle(btn) {
  try {
    const body = btn.closest('.npp-body');
    if (!body) return;
    const short = body.querySelector('.npp-text-short');
    const full  = body.querySelector('.npp-text-full');
    if (!short || !full) return;
    const isExpanded = full.style.display !== 'none';
    short.style.display = isExpanded ? 'inline' : 'none';
    full.style.display  = isExpanded ? 'none'   : 'block';
    btn.textContent = translateLiteral(isExpanded ? '展开' : '收起');
  } catch (_) {}
}

// ══════════════════════════════════════════════════════════════════════════
// P1: Agent Quick Cards (three-column)
// ══════════════════════════════════════════════════════════════════════════
function renderAgentQuickCards(agentHealth, timeline, status) {
  try {
    if (!agentHealth) return;
    const agents = agentHealth.agents || {};
    const summ = (timeline && timeline.summary) || {};

    // ── Main ──
    const main = agents.main || {};
    setText('aqc-main-dot', agentStatusDot(main.freshness));
    setText('aqc-main-mode', humanize(main.mode));
    const mainActions = [
      summ.posts_24h     != null ? formatPostCount(summ.posts_24h)   : null,
      summ.curations_24h != null ? formatCurationCount(summ.curations_24h) : null,
    ].filter(Boolean).join(' · ') || '—';
    setText('aqc-main-actions', mainActions);
    // Resources: read from bookmarker alloc (main controls budget)
    try {
      const ba = (status && status.main && status.main.budget_allocation) || {};
      const alloc = ba.allocations || {};
      const bmA = alloc.bookmarker || {};
      const op = bmA.op_budget != null ? Math.round(bmA.op_budget) : (main.op != null ? Math.round(main.op) : '—');
      const vpRaw = bmA.vp_budget ?? main.vp;
      const vp = vpRaw != null ? fmtNum(vpRaw) : '—';
      setText('aqc-main-resources', formatResourcePair(op, vp));
    } catch (_) { setText('aqc-main-resources', '—'); }
    setText('aqc-main-next', humanize(main.next_action));

    // ── Bookmarker ──
    const bm = agents.bookmarker || {};
    setText('aqc-bookmarker-dot', agentStatusDot(bm.freshness));
    setText('aqc-bookmarker-mode', humanize(bm.mode));
    setText('aqc-bookmarker-actions', summ.curations_24h != null ? formatCurationCount(summ.curations_24h) : '—');
    try {
      const bmOp = bm.op != null ? Math.round(bm.op) : '—';
      const bmVp = bm.vp != null ? fmtNum(bm.vp) : '—';
      setText('aqc-bookmarker-resources', formatResourcePair(bmOp, bmVp));
    } catch (_) { setText('aqc-bookmarker-resources', '—'); }
    setText('aqc-bookmarker-next', humanize(bm.next_action));

    // ── Trader ──
    const trader = agents.trader || {};
    setText('aqc-trader-dot', agentStatusDot(trader.freshness));
    setText('aqc-trader-mode', humanize(trader.mode));
    const traderActions = summ.trades_24h != null ? formatTradeCount(summ.trades_24h)
      : summ.claims_24h != null ? formatClaimCount(summ.claims_24h) : '—';
    setText('aqc-trader-actions', traderActions);
    try {
      const trStatus = (status && status.trader) || {};
      const onchain = trStatus.onchain_positions || {};
      const pUsd = onchain.total_portfolio_usd ?? trader.portfolio_usd_raw;
      setText('aqc-trader-resources', pUsd != null ? formatPortfolioUsd(parseFloat(pUsd).toFixed(2)) : '—');
    } catch (_) { setText('aqc-trader-resources', '—'); }
    setText('aqc-trader-next', humanize(trader.next_action));
  } catch (e) { console.warn('[renderAgentQuickCards]', e); }
}

// ══════════════════════════════════════════════════════════════════════════
// P10: Explainability Panel
// ══════════════════════════════════════════════════════════════════════════

function renderExplainability(data) {
  if (!data) return;
  _renderExplainArtifacts(data.artifacts || []);
  _renderExplainHealth(data.health || {});
  _renderExplainEvents(data.recent_events || []);
}

function _renderExplainArtifacts(artifacts) {
  const el = $('explain-artifact-list');
  if (!el) return;
  if (!artifacts.length) { el.innerHTML = `<div class="muted small">${langText('无制品数据', 'No artifact data')}</div>`; return; }

  el.innerHTML = artifacts.map((a, idx) => {
    const exists = a.exists;
    const badgeCls = !exists ? 'missing' : (a.age_hours != null && a.age_hours > 24) ? 'stale' : 'ok';
    const badgeText = !exists ? langText('缺失', 'missing') : (a.age_hours != null ? formatAgeText(a.age_hours) : 'ok');

    // Meta fields
    let metaHtml = '';
    if (a.meta) {
      const entries = Object.entries(a.meta).slice(0, 4);
      metaHtml = entries.map(([k, v]) => {
        const display = v === true ? 'true' : v === false ? 'false' : String(v ?? '—');
        return `<span>${escHtml(k)}: ${escHtml(display.length > 30 ? display.slice(0, 27) + '…' : display)}</span>`;
      }).join('');
    }

    // Provenance
    let provHtml = '';
    if (a.provenance) {
      const p = a.provenance;
      provHtml = `<div class="explain-artifact-prov">⛓ ${escHtml(p.producer || '?')}`;
      if (p.source_refs && p.source_refs.length) {
        provHtml += ` ← ${p.source_refs.slice(0, 3).map(s => escHtml(String(s).split('/').pop())).join(', ')}`;
      }
      if (p.raw_path) {
        provHtml += ` <span class="explain-raw-link" title="${escHtml(p.raw_path)}">📎 sidecar</span>`;
      }
      provHtml += '</div>';
    }

    // Raw file link
    const rawLink = a.raw_path ? `<span class="explain-raw-link" title="${escHtml(a.raw_path)}">📂 ${escHtml(a.raw_path)}</span>` : '';

    // Detail expand section (collapsed by default)
    let detailHtml = '';
    if (a.detail) {
      const detailEntries = Object.entries(a.detail).slice(0, 10);
      const detailRows = detailEntries.map(([k, v]) => {
        const display = v === true ? 'true' : v === false ? 'false' : Array.isArray(v) ? JSON.stringify(v) : String(v ?? '—');
        return `<div class="explain-detail-row"><span class="k">${escHtml(k)}</span><span class="v mono">${escHtml(display.length > 60 ? display.slice(0, 57) + '…' : display)}</span></div>`;
      }).join('');
      detailHtml = `<div class="explain-detail-toggle" onclick="this.nextElementSibling.classList.toggle('open');this.textContent=this.nextElementSibling.classList.contains('open')?'▲ ${langText('收起','Collapse')}':'▼ ${langText('展开详情','Expand detail')}'">▼ ${langText('展开详情', 'Expand detail')}</div><div class="explain-detail-body">${detailRows}</div>`;
    }

    return `<div class="explain-artifact-card">
      <div class="explain-artifact-header">
        <span class="explain-artifact-label">${escHtml(a.label)}</span>
        <span class="explain-artifact-badge ${badgeCls}">${escHtml(badgeText)}</span>
      </div>
      <div class="explain-artifact-meta">${metaHtml || '<span class="muted">—</span>'}</div>
      ${rawLink}
      ${provHtml}
      ${detailHtml}
    </div>`;
  }).join('');
}

function _renderExplainHealth(health) {
  const el = $('explain-health-ctx');
  if (!el) return;

  const overall = health.overall || 'unknown';
  const overallCls = overall === 'ok' ? 'clr-ok' : 'clr-warn';

  const contract = health.contract || {};
  const maint = health.maintenance || {};
  const lint = health.lint || {};

  const contractCls = (contract.severity === 'clear') ? 'clr-ok' : 'clr-warn';
  const maintCls = (maint.severity === 'clear') ? 'clr-ok' : 'clr-warn';
  const lintCls = (lint.needs_attention) ? 'clr-warn' : 'clr-ok';
  const lintLabel = lint.health_score != null ? lint.health_score + '%' : (lint.needs_attention ? langText('需关注', 'attention') : 'ok');

  el.innerHTML = `<div class="explain-health-grid">
    <div class="explain-health-pill">
      <div class="explain-health-pill-label">${langText('总体', 'Overall')}</div>
      <div class="explain-health-pill-value ${overallCls}">${escHtml(overall.toUpperCase())}</div>
    </div>
    <div class="explain-health-pill">
      <div class="explain-health-pill-label">${langText('合约', 'Contract')}</div>
      <div class="explain-health-pill-value ${contractCls}">${escHtml((contract.severity || 'unknown').toUpperCase())}</div>
    </div>
    <div class="explain-health-pill">
      <div class="explain-health-pill-label">${langText('维护', 'Maintenance')}</div>
      <div class="explain-health-pill-value ${maintCls}">${escHtml((maint.severity || 'unknown').toUpperCase())}</div>
    </div>
    <div class="explain-health-pill">
      <div class="explain-health-pill-label">${langText('Lint', 'Lint')}</div>
      <div class="explain-health-pill-value ${lintCls}">${escHtml(lintLabel)}</div>
    </div>
  </div>`;
}

function _renderExplainEvents(events) {
  const el = $('explain-events-list');
  if (!el) return;
  if (!events.length) { el.innerHTML = `<div class="muted small">${langText('无事件', 'No events')}</div>`; return; }

  el.innerHTML = events.map(e => {
    const ts = e.ts ? shortTs(e.ts) : '—';
    const stCls = e.status === 'ok' ? 'clr-ok' : 'clr-warn';
    return `<div class="explain-event-row">
      <span class="explain-event-ts">${escHtml(ts)}</span>
      <span class="explain-event-type">${escHtml(e.event_type || '?')}</span>
      <span class="explain-event-status ${stCls}">${escHtml(e.status || '?')}</span>
      <span class="explain-event-summary">${escHtml(e.summary || '')}</span>
    </div>`;
  }).join('');
}

// ══════════════════════════════════════════════════════════════════════════
// Main fetch loop
// ══════════════════════════════════════════════════════════════════════════
async function fetchAll() {
  try {
    const [status, timeline, autoresearch, controlTower, agentHealth, noc, explainability] = await Promise.all([
      fetchJSON('/api/status'),
      fetchJSON('/api/timeline'),
      fetchJSON('/api/autoresearch').catch(() => null),
      fetchJSON('/api/control-tower').catch(() => null),
      fetchJSON('/api/agent-health').catch(() => null),
      fetchJSON('/api/noc').catch(() => null),
      fetchJSON('/api/explainability').catch(() => null),
    ]);
    _lastStatus = status;
    _lastTimeline = timeline;
    _lastAutoResearch = autoresearch;
    _lastControlTower = controlTower;
    _lastAgentHealth = agentHealth;
    _lastNoc = noc;
    _lastExplainability = explainability;
    renderStatus(status);
    // Bootstrap banner: show if freshly installed environment
    const bannerEl = $('bootstrap-banner');
    if (bannerEl) {
      if (status && status.is_bootstrap) bannerEl.classList.add('visible');
      else bannerEl.classList.remove('visible');
    }
    renderTimeline(timeline);
    if (timeline && timeline.summary) renderTimelineSummary(timeline.summary);
    if (autoresearch) renderAutoResearch(autoresearch);
    if (controlTower) renderControlTower(controlTower);
    if (agentHealth) renderAgentHealth(agentHealth);
    if (noc) renderNoc(noc);
    if (explainability) renderExplainability(explainability);
    renderHeroBar(controlTower, timeline, agentHealth, status);
    renderNextPostPreview(status);
    translateDomTextNodes();
    // renderAgentQuickCards removed (cards deleted from UI)
  } catch (e) {
    showError(t('fetch-error') + e.message);
    console.error(e);
  }
}

// Auto-refresh every 30 seconds
fetchAll();
setInterval(fetchAll, 30_000);

// ── Init language ──────────────────────────────────────────────────────────
applyLang();
