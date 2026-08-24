import { useEffect, useMemo, useState } from 'react'
import { useDashboardContext } from '../App.jsx'
import { fetchCanonFacts, formatApiError } from '../api.js'
import Badge from '../components/Badge.jsx'
import DataTable from '../components/DataTable.jsx'
import { activeFactSummary, buildActiveFactRecords } from '../lib/facts.js'
import { formatChapterLabel, formatTableValue } from '../lib/format.js'

function shortDigest(value, length = 10) {
    const text = String(value || '')
    return text ? text.slice(0, length) : '—'
}

export default function FactsPage() {
    const { refreshToken } = useDashboardContext()
    const [facts, setFacts] = useState([])
    const [binding, setBinding] = useState(null)
    const [error, setError] = useState('')
    const [family, setFamily] = useState('')

    useEffect(() => {
        let cancelled = false
        fetchCanonFacts()
            .then(payload => {
                if (cancelled) return
                setFacts(buildActiveFactRecords(payload))
                setBinding(payload?.binding || null)
                setError('')
            })
            .catch(fetchError => {
                if (cancelled) return
                setFacts([])
                setBinding(null)
                setError(formatApiError(fetchError))
            })
        return () => {
            cancelled = true
        }
    }, [refreshToken])

    const families = useMemo(
        () => [...new Set(facts.map(row => row.category).filter(Boolean))].sort(),
        [facts],
    )
    const visibleFacts = useMemo(
        () => family ? facts.filter(row => row.category === family) : facts,
        [facts, family],
    )
    const summary = useMemo(() => activeFactSummary(facts), [facts])

    return (
        <section className="dashboard-page">
            <header className="page-header">
                <h2>Canon 活动事实</h2>
                <div className="header-badges">
                    {binding ? (
                        <Badge tone="blue">
                            HEAD {shortDigest(binding.head_hash)} · G{binding.generation ?? 0}
                        </Badge>
                    ) : null}
                    <Badge tone={error ? 'red' : 'green'}>{error ? 'unavailable' : `${facts.length} 条`}</Badge>
                </div>
            </header>

            <div className="authority-banner">
                <strong>只显示当前 HEAD 已生效事实</strong>
                <span>初始化与已认证迁移、已发布章节和作者长期硬设定在此合并展示；STAGING、旧索引和文风偏好不会进入本页。</span>
            </div>

            {error ? (
                <div className="authority-banner error" role="alert">
                    <strong>HEAD-bound 事实未就绪</strong>
                    <span>{error}</span>
                </div>
            ) : null}

            <div className="stat-grid">
                <article className="card stat-card">
                    <span className="stat-label">全部活动事实</span>
                    <span className="stat-value plain">{summary.total}</span>
                    <span className="stat-sub">as-of {Number(binding?.as_of_chapter) ? formatChapterLabel(binding.as_of_chapter) : 'Genesis'}</span>
                </article>
                <article className="card stat-card">
                    <span className="stat-label">初始化 / 迁移</span>
                    <span className="stat-value plain">{summary.genesis}</span>
                    <span className="stat-sub">已进入 v3 HEAD 的基线事实</span>
                </article>
                <article className="card stat-card">
                    <span className="stat-label">作者硬设定</span>
                    <span className="stat-value plain">{summary.authorAxioms}</span>
                    <span className="stat-sub">仅人工发布后的长期客观设定</span>
                </article>
                <article className="card stat-card">
                    <span className="stat-label">章节事实</span>
                    <span className="stat-value plain">{summary.chapters}</span>
                    <span className="stat-sub">当前 manifest 中的活动 slot</span>
                </article>
            </div>

            <div className="filter-group">
                <button
                    type="button"
                    className={`filter-btn ${family === '' ? 'active' : ''}`.trim()}
                    onClick={() => setFamily('')}
                >
                    全部
                </button>
                {families.map(item => (
                    <button
                        key={item}
                        type="button"
                        className={`filter-btn ${family === item ? 'active' : ''}`.trim()}
                        onClick={() => setFamily(item)}
                    >
                        {item}
                    </button>
                ))}
            </div>

            <article className="card">
                <div className="card-header">
                    <div>
                        <div className="section-label">ACTIVE CANON ONLY</div>
                        <div className="card-title">当前活动事实</div>
                    </div>
                    <Badge tone="green">usable as Canon</Badge>
                </div>
                <DataTable
                    columns={[
                        { key: 'originLabel', label: '来源', render: row => <Badge tone={row.origin === 'chapter_commit' ? 'green' : 'blue'}>{row.originLabel}</Badge> },
                        { key: 'category', label: '事实类型' },
                        { key: 'subject', label: '主体' },
                        { key: 'field', label: '字段 / Slot' },
                        { key: 'value', label: '当前值', render: row => <pre className="command-preview">{formatTableValue(row.value)}</pre> },
                        { key: 'chapter', label: '来源章节', render: row => row.chapter ? formatChapterLabel(row.chapter) : 'Genesis' },
                        { key: 'digest', label: 'Fact Digest', render: row => <code>{shortDigest(row.digest, 16)}</code> },
                    ]}
                    rows={visibleFacts}
                    rowKey="id"
                    pageSize={12}
                    emptyText={error ? '事实接口不可用' : '当前 HEAD 尚无活动事实'}
                    minWidth={1120}
                />
            </article>
        </section>
    )
}
