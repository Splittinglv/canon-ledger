import { useEffect, useMemo, useState } from 'react'
import { useDashboardContext } from '../App.jsx'
import Badge from '../components/Badge.jsx'
import DataTable from '../components/DataTable.jsx'
import { fetchCanonObligations, formatApiError } from '../api.js'
import { buildForeshadowingRecords, summarizeForeshadowing } from '../lib/foreshadowing.js'
import { formatChapterLabel } from '../lib/format.js'

function StatCard({ label, value }) {
    return (
        <article className="card stat-card">
            <span className="stat-label">{label}</span>
            <span className="stat-value plain">{value}</span>
        </article>
    )
}

function shortDigest(value, length = 14) {
    const text = String(value || '')
    return text ? text.slice(0, length) : '—'
}

export default function ForeshadowingPage() {
    const { workflow, refreshToken } = useDashboardContext()
    const [filter, setFilter] = useState('all')
    const [payload, setPayload] = useState(null)
    const [error, setError] = useState('')

    useEffect(() => {
        let cancelled = false
        fetchCanonObligations()
            .then(result => {
                if (cancelled) return
                setPayload(result)
                setError('')
            })
            .catch(cause => {
                if (cancelled) return
                setPayload(null)
                setError(formatApiError(cause))
            })
        return () => {
            cancelled = true
        }
    }, [refreshToken])

    const records = useMemo(() => buildForeshadowingRecords(payload), [payload])
    const summary = useMemo(() => summarizeForeshadowing(records), [records])
    const tableRows = useMemo(() => {
        if (filter === 'active') return records.filter(row => row.level === 'active')
        if (filter === 'resolved') return records.filter(row => row.level === 'resolved')
        return records
    }, [filter, records])

    const binding = payload?.binding || null
    const bindingMatches = Boolean(
        binding
        && workflow
        && binding.head_hash === workflow.head_hash
        && Number(binding.generation) === Number(workflow.generation),
    )

    return (
        <section className="dashboard-page">
            <header className="page-header">
                <h2>Canon 开放问题</h2>
                <div className="header-badges">
                    <Badge tone="cyan">{formatChapterLabel(payload?.latest_chapter || workflow?.latest_chapter || 0)}</Badge>
                    {binding ? (
                        <Badge tone={bindingMatches ? 'green' : 'red'}>
                            HEAD {shortDigest(binding.head_hash)} · G{binding.generation}
                        </Badge>
                    ) : null}
                </div>
            </header>

            {error ? (
                <div className="authority-banner error" role="alert">
                    <strong>HEAD-bound obligations 未就绪</strong>
                    <span>{error}</span>
                </div>
            ) : null}

            <div className="stat-grid">
                <StatCard label="全部 Open Loops" value={String(summary.total)} />
                <StatCard label="当前活跃" value={String(summary.active)} />
                <StatCard label="已经回收" value={String(summary.resolved)} />
                <StatCard label="Authority" value={bindingMatches ? 'exact' : 'unavailable'} />
            </div>

            <div className="filter-group">
                <button type="button" className={`filter-btn ${filter === 'all' ? 'active' : ''}`.trim()} onClick={() => setFilter('all')}>全部</button>
                <button type="button" className={`filter-btn ${filter === 'active' ? 'active' : ''}`.trim()} onClick={() => setFilter('active')}>活跃</button>
                <button type="button" className={`filter-btn ${filter === 'resolved' ? 'active' : ''}`.trim()} onClick={() => setFilter('resolved')}>已回收</button>
            </div>

            <article className="card">
                <div className="card-header">
                    <div>
                        <div className="section-label">CANON OBLIGATIONS</div>
                        <div className="card-title">HEAD-bound Open Loop 历史</div>
                    </div>
                    <Badge tone="blue">{tableRows.length} 条</Badge>
                </div>
                {error ? null : (
                    <DataTable
                        columns={[
                            { key: 'content', label: '开放问题' },
                            {
                                key: 'statusText',
                                label: '状态',
                                render: row => <Badge tone={row.level === 'resolved' ? 'green' : 'blue'}>{row.statusText}</Badge>,
                            },
                            { key: 'plantedChapter', label: '提出章', render: row => formatChapterLabel(row.plantedChapter) },
                            { key: 'resolvedChapter', label: '回收章', render: row => row.resolvedChapter ? formatChapterLabel(row.resolvedChapter) : '—' },
                            { key: 'resolution', label: '回收事实', render: row => row.resolution || '—' },
                            { key: 'factDigest', label: 'Fact Digest', render: row => <code>{shortDigest(row.factDigest)}</code> },
                        ]}
                        rows={tableRows}
                        rowKey="id"
                        pageSize={12}
                        emptyText="当前 HEAD 没有 open-loop 事实"
                        minWidth={920}
                    />
                )}
            </article>
        </section>
    )
}
