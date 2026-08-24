import { startTransition, useCallback, useEffect, useState } from 'react'
import { NavLink, Outlet, useOutletContext } from 'react-router-dom'
import { fetchProjectInfo, fetchWorkflow, formatApiError, subscribeSSE } from './api.js'
import {
    BookmarkIcon,
    ChartBarIcon,
    FolderIcon,
    LedgerIcon,
    SlidersIcon,
    UsersIcon,
    WifiIcon,
    WifiOffIcon,
} from './icons.jsx'

const NAV_ITEMS = [
    { to: '/', label: '总览', icon: ChartBarIcon, end: true },
    { to: '/facts', label: 'Canon 事实', icon: LedgerIcon },
    { to: '/characters', label: '角色图鉴', icon: UsersIcon },
    { to: '/foreshadowing', label: '开放问题', icon: BookmarkIcon },
    { to: '/files', label: '文档浏览', icon: FolderIcon },
    { to: '/system', label: '系统状态', icon: SlidersIcon },
]

export default function App() {
    const [projectInfo, setProjectInfo] = useState(null)
    const [workflow, setWorkflow] = useState(null)
    const [workflowError, setWorkflowError] = useState('')
    const [refreshToken, setRefreshToken] = useState(0)
    const [connected, setConnected] = useState(false)

    const loadProjectInfo = useCallback(() => {
        Promise.allSettled([fetchProjectInfo(), fetchWorkflow()]).then(results => {
            const info = results[0].status === 'fulfilled' ? results[0].value : null
            const exactWorkflow = results[1].status === 'fulfilled' ? results[1].value : null
            setProjectInfo(info)
            setWorkflow(exactWorkflow)
            setWorkflowError(
                results[1].status === 'rejected'
                    ? formatApiError(results[1].reason)
                    : '',
            )
        })
    }, [])

    useEffect(() => {
        loadProjectInfo()
    }, [loadProjectInfo, refreshToken])

    useEffect(() => {
        const unsubscribe = subscribeSSE(
            () => {
                startTransition(() => {
                    setRefreshToken(current => current + 1)
                })
            },
            {
                onOpen: () => setConnected(true),
                onError: () => setConnected(false),
            },
        )

        return () => {
            unsubscribe()
            setConnected(false)
        }
    }, [])

    const title = projectInfo?.project_info?.title || '未加载项目'
    const head = String(workflow?.head_hash || '')

    return (
        <div className="app-layout">
            <aside className="sidebar">
                <div className="sidebar-header">
                    <h1>叙典 CANONLEDGER</h1>
                    <div className="subtitle" title={title}>{title}</div>
                </div>
                <nav className="sidebar-nav">
                    {NAV_ITEMS.map(item => {
                        const Icon = item.icon
                        return (
                            <NavLink
                                key={item.to}
                                to={item.to}
                                end={item.end}
                                className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`.trim()}
                            >
                                <span className="icon">
                                    <Icon />
                                </span>
                                <span>{item.label}</span>
                            </NavLink>
                        )
                    })}
                </nav>
                <div className="sidebar-authority" aria-label="Canon v3 权威状态">
                    <div className="mini-label">CANON AUTHORITY</div>
                    <strong>{workflow?.state || 'unavailable'}</strong>
                    <span>HEAD {head ? head.slice(0, 10) : '—'} · G{workflow?.generation ?? '—'}</span>
                    <span>{workflow?.can_write_next ? '允许写下一章' : '当前不可写'}</span>
                    {workflowError ? <span className="authority-error">{workflowError}</span> : null}
                </div>
                <div className="live-indicator">
                    <span className="icon">
                        {connected ? <WifiIcon /> : <WifiOffIcon />}
                    </span>
                    {connected ? '实时同步中' : '实时连接断开'}
                </div>
            </aside>

            <main className="main-content">
                <Outlet
                    context={{
                        projectInfo,
                        workflow,
                        workflowError,
                        refreshToken,
                        connected,
                        reloadProjectInfo: loadProjectInfo,
                    }}
                />
            </main>
        </div>
    )
}

export function useDashboardContext() {
    return useOutletContext()
}
