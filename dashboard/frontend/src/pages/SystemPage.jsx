import { useEffect, useMemo, useState } from 'react'
import { useDashboardContext } from '../App.jsx'
import Badge from '../components/Badge.jsx'
import DataTable from '../components/DataTable.jsx'
import { fetchCommits, fetchRetrievalStatus, formatApiError } from '../api.js'
import { formatChapterLabel } from '../lib/format.js'

function statusTone(status) {
    const text = String(status || '').toLowerCase()
    if (text === 'ready' || text === 'accepted' || text === 'fresh') return 'green'
    if (text.includes('invalid') || text.includes('rewrite')) return 'red'
    if (text.includes('required') || text.includes('awaiting')) return 'amber'
    return 'blue'
}

function shortDigest(value, length = 12) {
    const text = String(value || '')
    return text ? text.slice(0, length) : '—'
}

function StatCard({ label, value, sub }) {
    return (
        <article className="card stat-card">
            <span className="stat-label">{label}</span>
            <span className="stat-value plain">{value}</span>
            <span className="stat-sub">{sub}</span>
        </article>
    )
}

export default function SystemPage() {
    const { workflow, workflowError, refreshToken } = useDashboardContext()
    const [commits, setCommits] = useState([])
    const [commitBinding, setCommitBinding] = useState(null)
    const [commitError, setCommitError] = useState('')
    const [retrieval, setRetrieval] = useState(null)
    const [retrievalError, setRetrievalError] = useState('')

    useEffect(() => {
        let cancelled = false
        fetchCommits({ limit: 20 })
            .then(payload => {
                if (cancelled) return
                setCommits(payload?.items || [])
                setCommitBinding(payload?.binding || null)
                setCommitError('')
            })
            .catch(error => {
                if (cancelled) return
                setCommits([])
                setCommitBinding(null)
                setCommitError(formatApiError(error))
            })
        return () => {
            cancelled = true
        }
    }, [refreshToken])

    useEffect(() => {
        let cancelled = false
        fetchRetrievalStatus()
            .then(payload => {
                if (cancelled) return
                setRetrieval(payload || null)
                setRetrievalError('')
            })
            .catch(error => {
                if (cancelled) return
                setRetrieval(null)
                setRetrievalError(formatApiError(error))
            })
        return () => {
            cancelled = true
        }
    }, [refreshToken])

    const protocolRows = useMemo(() => [
        { name: 'Workflow schema', value: workflow?.schema_version || '—' },
        { name: 'Engine schema', value: workflow?.engine_schema_version || '—' },
        { name: 'Bootstrap mode', value: workflow?.bootstrap_mode || '—' },
        { name: 'Transaction kind', value: workflow?.transaction_kind || '—' },
        { name: 'Author axiom digest', value: workflow?.author_axiom_digest || '—' },
        { name: 'Entity registry digest', value: workflow?.entity_registry_digest || '—' },
        { name: 'Finalize token', value: workflow?.finalize_token || '—' },
    ], [workflow])

    const bindingMatches = Boolean(
        commitBinding
        && workflow
        && commitBinding.head_hash === workflow.head_hash
        && Number(commitBinding.generation) === Number(workflow.generation),
    )

    return (
        <section className="dashboard-page">
            <header className="page-header">
                <h2>Canon v3 系统状态</h2>
                <Badge tone={statusTone(workflow?.state)}>{workflow?.state || 'unavailable'}</Badge>
            </header>

            {workflowError ? (
                <div className="authority-banner error" role="alert">
                    <strong>Workflow 无法读取</strong>
                    <span>{workflowError}</span>
                </div>
            ) : null}

            <div className="stat-grid">
                <StatCard
                    label="Authority"
                    value={workflow?.state || 'unavailable'}
                    sub={`action ${(workflow?.primary_action || {}).id || (workflow?.primary_action || {}).code || '—'}`}
                />
                <StatCard
                    label="HEAD / Generation"
                    value={`${shortDigest(workflow?.head_hash)} / G${workflow?.generation ?? '—'}`}
                    sub={workflow?.workflow_digest ? `workflow ${shortDigest(workflow.workflow_digest)}` : '没有 workflow binding'}
                />
                <StatCard
                    label="Canon Projection"
                    value={workflow?.projection_fresh ? 'fresh' : 'stale'}
                    sub={workflow?.projection_fresh ? '事实 API 可读取' : '事实 API 将结构化返回 409'}
                />
                <StatCard
                    label="Commit Binding"
                    value={bindingMatches ? 'exact' : 'unavailable'}
                    sub={commitBinding ? `${shortDigest(commitBinding.head_hash)} · G${commitBinding.generation}` : '未取得 HEAD-bound history'}
                />
                <StatCard
                    label="Optional Retrieval"
                    value={retrieval?.state || 'unavailable'}
                    sub={`${retrieval?.mode || 'bm25'} · ${retrieval?.fact_count ?? 0} facts / ${retrieval?.embedded_count ?? 0} vectors`}
                />
            </div>

            {retrievalError || (retrieval && retrieval.state !== 'ready') ? (
                <div className="authority-banner" role="status">
                    <strong>检索增强已降级，但不阻断写作</strong>
                    <span>
                        {retrievalError || `${retrieval.state} · 当前 active Canon 会以内存 BM25 召回；命中仍需回查事实。`}
                    </span>
                </div>
            ) : null}

            <article className="card">
                <div className="card-header">
                    <div>
                        <div className="section-label">PUBLIC PROTOCOL</div>
                        <div className="card-title">公开协议绑定</div>
                    </div>
                    <Badge tone="blue">GET-only</Badge>
                </div>
                <DataTable
                    columns={[
                        { key: 'name', label: '字段' },
                        { key: 'value', label: 'Exact Value', render: row => <code className="digest-cell">{row.value}</code> },
                    ]}
                    rows={protocolRows}
                    rowKey="name"
                    pageSize={10}
                    emptyText="Workflow 尚不可用"
                    minWidth={720}
                />
            </article>

            <article className="card">
                <div className="card-header">
                    <div>
                        <div className="section-label">CANON COMMIT HISTORY</div>
                        <div className="card-title">当前 HEAD 可达 Commit</div>
                    </div>
                    <Badge tone={commitError ? 'red' : 'green'}>{commitError ? 'unavailable' : `${commits.length} 条`}</Badge>
                </div>
                {commitError ? (
                    <div className="authority-banner error" role="alert">
                        <strong>HEAD-bound history 未就绪</strong>
                        <span>{commitError}</span>
                    </div>
                ) : (
                    <DataTable
                        columns={[
                            { key: 'chapter', label: '章节', render: row => formatChapterLabel(row.chapter) },
                            { key: 'revision', label: 'Revision' },
                            { key: 'status', label: '状态', render: row => <Badge tone="green">{row.status}</Badge> },
                            { key: 'projection_status', label: 'Canon projection', render: row => <Badge tone="green">{row.projection_status}</Badge> },
                            { key: 'commit_hash', label: 'Commit Hash', render: row => <code>{shortDigest(row.commit_hash, 16)}</code> },
                            { key: 'transaction_hash', label: 'Transaction', render: row => <code>{shortDigest(row.transaction_hash, 16)}</code> },
                        ]}
                        rows={commits}
                        rowKey="commit_hash"
                        pageSize={10}
                        emptyText="当前 HEAD 尚无章节 commit"
                        minWidth={920}
                    />
                )}
            </article>
        </section>
    )
}
