import { useMemo } from 'react'
import { useDashboardContext } from '../App.jsx'
import Badge from '../components/Badge.jsx'
import DataTable from '../components/DataTable.jsx'
import { formatChapterLabel } from '../lib/format.js'

function stateTone(state) {
    if (state === 'ready' || state === 'ready_to_finalize') return 'green'
    if (state === 'awaiting_human' || state === 'recompile_required') return 'amber'
    if (state === 'rewrite_required' || state === 'invalid') return 'red'
    return 'blue'
}

function shortDigest(value, length = 12) {
    const text = String(value || '')
    return text ? text.slice(0, length) : '—'
}

function actionName(action) {
    if (typeof action === 'string') return action
    return action?.id || action?.code || action?.value || 'unknown'
}

function caseStatus(item) {
    if (item?.decision_head_hash) return '已裁决'
    if (item?.requires_rewrite) return '需改写'
    return '待人工确认'
}

function StatCard({ label, value, sub, tone = 'plain' }) {
    return (
        <article className="card stat-card">
            <span className="stat-label">{label}</span>
            <span className={`stat-value ${tone === 'plain' ? 'plain' : ''}`.trim()}>{value}</span>
            <span className="stat-sub">{sub}</span>
        </article>
    )
}

function BindingRow({ label, value, code = false }) {
    return (
        <div className="binding-row">
            <span>{label}</span>
            {code ? <code>{value || '—'}</code> : <strong>{value || '—'}</strong>}
        </div>
    )
}

export default function OverviewPage() {
    const { projectInfo, workflow, workflowError } = useDashboardContext()
    const info = projectInfo?.project_info || {}
    const cases = Array.isArray(workflow?.cases) ? workflow.cases : []
    const primaryAction = workflow?.primary_action || {}
    const targetChapter = Number(workflow?.chapter || workflow?.expected_next_chapter || 0)
    const stageDigest = workflow?.stage_digest || null

    const caseRows = useMemo(() => cases.map(item => ({
        ...item,
        status_text: caseStatus(item),
        allowed_actions_text: (item?.allowed_actions || []).map(actionName).join(' / ') || '—',
    })), [cases])

    return (
        <section className="dashboard-page">
            <header className="page-header">
                <h2>Canon v3 总览</h2>
                <div className="header-badges">
                    <Badge tone={stateTone(workflow?.state)}>{workflow?.state || 'unavailable'}</Badge>
                    {info.genre ? <Badge tone="blue">{info.genre}</Badge> : null}
                </div>
            </header>

            {workflowError ? (
                <div className="authority-banner error" role="alert">
                    <strong>Workflow 无法读取</strong>
                    <span>{workflowError}</span>
                </div>
            ) : null}

            <div className="stat-grid">
                <StatCard
                    label="Exact Workflow State"
                    value={workflow?.state || 'unavailable'}
                    sub={`协议 ${workflow?.schema_version || '—'}`}
                />
                <StatCard
                    label="当前 HEAD"
                    value={shortDigest(workflow?.head_hash)}
                    sub={`generation ${workflow?.generation ?? '—'}`}
                />
                <StatCard
                    label="目标章节"
                    value={targetChapter ? formatChapterLabel(targetChapter) : '—'}
                    sub={`最新 Canon ${formatChapterLabel(workflow?.latest_chapter || 0)}`}
                />
                <StatCard
                    label="是否可写"
                    value={workflow?.can_write_next ? 'YES' : 'NO'}
                    sub={workflow?.can_finalize ? '当前事务可以发布' : '当前事务不可发布'}
                    tone={workflow?.can_write_next ? 'accent' : 'plain'}
                />
                <StatCard
                    label="当前 STAGING"
                    value={stageDigest ? shortDigest(stageDigest) : 'NONE'}
                    sub={stageDigest ? (workflow?.transaction_kind || 'chapter') : '没有未发布事务'}
                />
                <StatCard
                    label="人工 Case"
                    value={String(cases.length)}
                    sub={`required ${workflow?.counts?.required || 0} · rewrite ${workflow?.counts?.rewrite || 0}`}
                />
            </div>

            <div className="content-grid two-columns authority-grid">
                <article className="card">
                    <div className="card-header">
                        <div>
                            <div className="section-label">EXACT BINDING</div>
                            <div className="card-title">权威绑定</div>
                        </div>
                        <Badge tone={workflow?.projection_fresh ? 'green' : 'red'}>
                            projection {workflow?.projection_fresh ? 'fresh' : 'stale'}
                        </Badge>
                    </div>
                    <div className="binding-list">
                        <BindingRow label="HEAD" value={workflow?.head_hash} code />
                        <BindingRow label="Generation" value={String(workflow?.generation ?? '—')} />
                        <BindingRow label="Workflow Digest" value={workflow?.workflow_digest} code />
                        <BindingRow label="Authority View Digest" value={workflow?.authority_view_digest} code />
                        <BindingRow label="Transaction" value={workflow?.transaction_hash} code />
                        <BindingRow label="Stage Digest" value={workflow?.stage_digest} code />
                    </div>
                </article>

                <article className="card primary-action-card">
                    <div className="card-header">
                        <div>
                            <div className="section-label">PRIMARY ACTION</div>
                            <div className="card-title">唯一下一步</div>
                        </div>
                        <Badge tone="purple">只读展示</Badge>
                    </div>
                    <div className="primary-action-body">
                        <strong>{primaryAction.label || primaryAction.id || primaryAction.code || '暂无动作'}</strong>
                        <code>{primaryAction.id || primaryAction.code || '—'}</code>
                        <pre className="command-preview">{primaryAction.command || '—'}</pre>
                        <p>Dashboard 不执行人工裁决或 Canon 写入；请在 Cursor 中执行上面的权威 Skill/CLI 动作。</p>
                    </div>
                </article>
            </div>

            <article className="card">
                <div className="card-header">
                    <div>
                        <div className="section-label">HUMAN REVIEW CASES</div>
                        <div className="card-title">当前人工审核</div>
                    </div>
                    <Badge tone={cases.length ? 'amber' : 'green'}>{cases.length} 个 case</Badge>
                </div>
                <DataTable
                    columns={[
                        { key: 'case_key', label: 'Case Key' },
                        { key: 'kind', label: '类型', render: row => row.kind || row.case_kind || row.operation || '—' },
                        { key: 'level', label: '级别', render: row => row.level || '—' },
                        {
                            key: 'status_text',
                            label: '状态',
                            render: row => <Badge tone={row.decision_head_hash ? 'green' : 'amber'}>{row.status_text}</Badge>,
                        },
                        { key: 'allowed_actions_text', label: '允许动作' },
                    ]}
                    rows={caseRows}
                    rowKey="case_key"
                    pageSize={10}
                    emptyText="当前没有人工审核 case"
                    minWidth={900}
                />
            </article>
        </section>
    )
}
