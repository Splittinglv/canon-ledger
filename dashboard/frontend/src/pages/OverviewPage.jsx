import { useEffect, useMemo, useState } from 'react'
import { useDashboardContext } from '../App.jsx'
import { fetchChapterTrend, fetchChapters, fetchStoryRuntimeHealth } from '../api.js'
import Badge from '../components/Badge.jsx'
import ChartWrapper from '../components/ChartWrapper.jsx'
import DataTable from '../components/DataTable.jsx'
import Pager from '../components/Pager.jsx'
import { formatChapterLabel, formatNumber, formatPercent, formatShortNumber } from '../lib/format.js'
import { buildForeshadowingRecords, summarizeForeshadowing } from '../lib/foreshadowing.js'
import { groupChaptersByVolume } from '../lib/story.js'

const WINDOW_SIZE = 50

const FORESHADOW_PRIORITY = {
    overdue: 4,
    urgent: 3,
    active: 2,
    resolved: 1,
}

function toneForForeshadowing(level) {
    if (level === 'overdue') return 'red'
    if (level === 'urgent') return 'amber'
    if (level === 'resolved') return 'green'
    return 'blue'
}

function countReviewIssues(item) {
    if (item?.issues_count != null && Number.isFinite(Number(item.issues_count))) {
        return Number(item.issues_count)
    }
    const counts = item?.review_severity_counts || {}
    return Object.values(counts).reduce((sum, value) => {
        const n = Number(value)
        return sum + (Number.isFinite(n) ? n : 0)
    }, 0)
}

function countBlockingIssues(item) {
    if (item?.blocking_count != null && Number.isFinite(Number(item.blocking_count))) {
        return Number(item.blocking_count)
    }
    const counts = item?.review_severity_counts || {}
    return Number(counts.critical || 0)
}

function toneForUrgencyBadge(level) {
    if (level === 'critical') return 'red'
    if (level === 'high') return 'amber'
    if (level === 'medium') return 'amber'
    if (level === 'resolved') return 'green'
    return 'blue'
}

function formatRuntimeText(runtimeHealth) {
    if (!runtimeHealth) return '运行态未加载'
    const fallback = Array.isArray(runtimeHealth.fallback_sources) && runtimeHealth.fallback_sources.length
        ? runtimeHealth.fallback_sources.join(' / ')
        : 'none'
    return `${runtimeHealth.latest_commit_status || 'missing'} · fallback ${fallback}`
}

function buildIssueOption(items) {
    const issueCounts = items.map(item => countReviewIssues(item))
    const blockingCounts = items.map(item => countBlockingIssues(item))

    return {
        tooltip: { trigger: 'axis' },
        legend: { bottom: 0 },
        grid: { left: 52, right: 24, top: 36, bottom: 46 },
        xAxis: {
            type: 'category',
            data: items.map(item => item.chapter),
            axisLabel: { interval: 9, formatter: value => `${value}` },
        },
        yAxis: {
            type: 'value',
            min: 0,
            minInterval: 1,
            max: Math.max(1, ...issueCounts, ...blockingCounts),
        },
        series: [
            {
                type: 'bar',
                name: '问题',
                data: issueCounts,
                barWidth: '36%',
                itemStyle: {
                    color: '#26a8ff',
                    borderColor: '#2a220f',
                    borderWidth: 2,
                },
            },
            {
                type: 'bar',
                name: '阻断',
                data: blockingCounts,
                barWidth: '36%',
                itemStyle: {
                    color: '#d7263d',
                    borderColor: '#2a220f',
                    borderWidth: 2,
                },
            },
        ],
    }
}

function buildVolumeOption(groups) {
    return {
        tooltip: { trigger: 'axis' },
        xAxis: {
            type: 'category',
            data: groups.map(group => group.label),
        },
        yAxis: {
            type: 'value',
            axisLabel: {
                formatter: value => `${formatShortNumber(Number(value) / 10000)}万`,
            },
        },
        series: [
            {
                type: 'bar',
                data: groups.map(group => group.totalWords),
                barWidth: '56%',
                itemStyle: {
                    color: '#7f5af0',
                    borderColor: '#2a220f',
                    borderWidth: 2,
                },
                label: {
                    show: true,
                    position: 'top',
                    formatter: params => formatNumber(params.value),
                    color: '#5d5035',
                    fontSize: 12,
                },
            },
        ],
    }
}

function StatCard({ label, value, sub, tone = 'accent', progress }) {
    return (
        <article className="card stat-card">
            <span className="stat-label">{label}</span>
            <span className={`stat-value ${tone === 'plain' ? 'plain' : ''}`.trim()}>{value}</span>
            <span className="stat-sub">{sub}</span>
            {progress !== undefined ? (
                <div className="progress-track">
                    <div className="progress-fill" style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} />
                </div>
            ) : null}
        </article>
    )
}

function RecentSummaryCard({ item }) {
    return (
        <article className="summary-card">
            <div className="summary-card-header">
                <span className="summary-chapter">{formatChapterLabel(item.chapter)}</span>
            </div>
            <h3>{item.title || '未命名章节'}</h3>
            <p>{item.summary || '暂无章节概要。'}</p>
            <div className="summary-meta">
                <span>{item.location || '未记录地点'}</span>
                <span>{formatNumber(item.word_count)} 字</span>
                <span>{(item.characters || []).join('、') || '未记录角色'}</span>
            </div>
        </article>
    )
}

export default function OverviewPage() {
    const { projectInfo, refreshToken } = useDashboardContext()
    const [runtimeHealth, setRuntimeHealth] = useState(null)
    const [allChapters, setAllChapters] = useState([])
    const [trendWindow, setTrendWindow] = useState({ items: [], total: 0, latest_chapter: 0 })
    const [latestWindow, setLatestWindow] = useState({ items: [], total: 0, latest_chapter: 0 })
    const [windowIndex, setWindowIndex] = useState(0)
    const [loadingTrend, setLoadingTrend] = useState(true)

    useEffect(() => {
        setWindowIndex(0)
    }, [refreshToken])

    useEffect(() => {
        let cancelled = false

        Promise.allSettled([
            fetchStoryRuntimeHealth(),
            fetchChapters(),
            fetchChapterTrend({ limit: WINDOW_SIZE, offset: 0 }),
        ]).then(results => {
            if (cancelled) return

            setRuntimeHealth(results[0].status === 'fulfilled' ? results[0].value : null)
            setAllChapters(results[1].status === 'fulfilled' ? results[1].value : [])

            const latest = results[2].status === 'fulfilled'
                ? results[2].value
                : { items: [], total: 0, latest_chapter: 0 }
            setLatestWindow(latest)
            setTrendWindow(latest)
            setLoadingTrend(false)
        })

        return () => {
            cancelled = true
        }
    }, [refreshToken])

    useEffect(() => {
        if (windowIndex === 0) {
            setTrendWindow(latestWindow)
            return
        }

        let cancelled = false
        setLoadingTrend(true)

        fetchChapterTrend({ limit: WINDOW_SIZE, offset: windowIndex * WINDOW_SIZE })
            .then(payload => {
                if (!cancelled) {
                    setTrendWindow(payload)
                }
            })
            .catch(() => {
                if (!cancelled) {
                    setTrendWindow({ items: [], total: latestWindow.total || 0, latest_chapter: latestWindow.latest_chapter || 0 })
                }
            })
            .finally(() => {
                if (!cancelled) {
                    setLoadingTrend(false)
                }
            })

        return () => {
            cancelled = true
        }
    }, [latestWindow, refreshToken, windowIndex])

    const info = projectInfo?.project_info || {}
    const progress = projectInfo?.progress || {}

    const windowItems = trendWindow.items || []
    const totalPages = Math.max(1, Math.ceil((trendWindow.total || latestWindow.total || 0) / WINDOW_SIZE))
    const displayPage = Math.max(1, totalPages - windowIndex)
    const currentStart = windowItems[0]?.chapter || 0
    const currentEnd = windowItems[windowItems.length - 1]?.chapter || 0

    const volumeGroups = useMemo(
        () => groupChaptersByVolume(allChapters, projectInfo),
        [allChapters, projectInfo],
    )

    const foreshadowRecords = useMemo(
        () => buildForeshadowingRecords(projectInfo),
        [projectInfo],
    )
    const foreshadowSummary = useMemo(
        () => summarizeForeshadowing(foreshadowRecords),
        [foreshadowRecords],
    )

    const urgentRows = useMemo(() => {
        return [...foreshadowRecords]
            .sort((left, right) => {
                const priority = (FORESHADOW_PRIORITY[right.level] || 0) - (FORESHADOW_PRIORITY[left.level] || 0)
                if (priority !== 0) return priority
                return (right.urgencyScore || 0) - (left.urgencyScore || 0)
            })
            .slice(0, 5)
    }, [foreshadowRecords])

    const latestIssueTotals = useMemo(() => {
        const items = latestWindow.items || []
        return {
            issues: items.reduce((sum, item) => sum + countReviewIssues(item), 0),
            blocking: items.reduce((sum, item) => sum + countBlockingIssues(item), 0),
        }
    }, [latestWindow.items])

    const recentSummaries = useMemo(() => {
        return [...(latestWindow.items || [])].slice(-3).reverse()
    }, [latestWindow.items])

    const totalWords = Number(progress.total_words || 0)
    const targetWords = Number(info.target_words || 0)
    const progressPercent = targetWords > 0 ? (totalWords / targetWords) * 100 : 0

    return (
        <section className="dashboard-page">
            <header className="page-header">
                <h2>总览</h2>
                <Badge tone="blue">{info.genre || '未知题材'}</Badge>
            </header>

            <div className="stat-grid">
                <StatCard
                    label="总字数"
                    value={formatNumber(totalWords)}
                    sub={`目标 ${formatNumber(targetWords || 0)} 字 · ${formatPercent(progressPercent)}`}
                    progress={progressPercent}
                />
                <StatCard
                    label="当前章节"
                    value={formatChapterLabel(progress.current_chapter)}
                    sub={`目标 ${info.target_chapters || '—'} 章 · 卷 ${progress.current_volume || '—'}`}
                />
                <StatCard
                    label="Story Runtime"
                    value={runtimeHealth?.mainline_ready ? 'Mainline' : 'Fallback'}
                    sub={formatRuntimeText(runtimeHealth)}
                    tone="plain"
                />
                <StatCard
                    label="审查问题"
                    value={formatNumber(latestIssueTotals.issues)}
                    sub={`阻断 ${formatNumber(latestIssueTotals.blocking)} · 最近 50 章`}
                />
                <StatCard
                    label="紧急伏笔"
                    value={String(foreshadowSummary.attention)}
                    sub={`总计 ${foreshadowSummary.total} 条伏笔`}
                />
            </div>

            <article className="card">
                <div className="card-header">
                    <div>
                        <div className="section-label">REVIEW TREND</div>
                        <div className="card-title">审查问题趋势</div>
                    </div>
                    <Badge tone="green">
                        {currentStart && currentEnd ? `${formatChapterLabel(currentStart)} - ${formatChapterLabel(currentEnd)}` : '最近窗口'}
                    </Badge>
                </div>
                {windowItems.length ? (
                    <>
                        <ChartWrapper option={buildIssueOption(windowItems)} loading={loadingTrend} />
                        <Pager
                            page={displayPage}
                            totalPages={totalPages}
                            currentStart={currentStart}
                            currentEnd={currentEnd}
                            totalItems={trendWindow.total || latestWindow.total || 0}
                            onPrevious={() => setWindowIndex(current => Math.min(totalPages - 1, current + 1))}
                            onNext={() => setWindowIndex(current => Math.max(0, current - 1))}
                            onLatest={() => setWindowIndex(0)}
                            stepLabel={String(WINDOW_SIZE)}
                        />
                    </>
                ) : (
                    <div className="empty-state">
                        <p>暂无章节趋势数据</p>
                    </div>
                )}
            </article>

            <article className="card">
                <div className="card-header">
                    <div>
                        <div className="section-label">WORD DISTRIBUTION</div>
                        <div className="card-title">字数分布（按卷）</div>
                    </div>
                    <Badge tone="purple">{volumeGroups.length} 卷</Badge>
                </div>
                {volumeGroups.length ? (
                    <ChartWrapper option={buildVolumeOption(volumeGroups)} />
                ) : (
                    <div className="empty-state">
                        <p>暂无章节字数数据</p>
                    </div>
                )}
            </article>

            <div className="content-grid two-columns">
                <article className="card">
                    <div className="card-header">
                        <div>
                            <div className="section-label">FORESHADOWING</div>
                            <div className="card-title">紧急伏笔 Top 5</div>
                        </div>
                        <Badge tone="amber">按紧急度排序</Badge>
                    </div>
                    <DataTable
                        columns={[
                            { key: 'content', label: '内容' },
                            {
                                key: 'statusText',
                                label: '状态',
                                render: row => <Badge tone={toneForForeshadowing(row.level)}>{row.statusText}</Badge>,
                            },
                            {
                                key: 'plantedChapter',
                                label: '埋设章',
                                render: row => formatChapterLabel(row.plantedChapter),
                            },
                            {
                                key: 'targetChapter',
                                label: '目标章',
                                render: row => formatChapterLabel(row.targetChapter),
                            },
                            {
                                key: 'urgencyText',
                                label: '紧急度',
                                render: row => <Badge tone={toneForUrgencyBadge(row.urgencyText)}>{row.urgencyText}</Badge>,
                            },
                        ]}
                        rows={urgentRows}
                        rowKey="id"
                        pageSize={5}
                        emptyText="暂无伏笔数据"
                        minWidth={680}
                    />
                </article>

                <article className="card">
                    <div className="card-header">
                        <div>
                            <div className="section-label">LATEST CHAPTERS</div>
                            <div className="card-title">最近 3 章概要</div>
                        </div>
                        <Badge tone="cyan">{recentSummaries.length} 条</Badge>
                    </div>
                    {recentSummaries.length ? (
                        <div className="summary-card-list">
                            {recentSummaries.map(item => (
                                <RecentSummaryCard key={item.chapter} item={item} />
                            ))}
                        </div>
                    ) : (
                        <div className="empty-state">
                            <p>暂无最新章节概要</p>
                        </div>
                    )}
                </article>
            </div>
        </section>
    )
}
