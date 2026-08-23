const BASE = ''

export class DashboardApiError extends Error {
    constructor(status, statusText, detail) {
        const code = detail?.code || `http_${status}`
        super(`${code}: ${detail?.message || statusText || 'Dashboard API 请求失败'}`)
        this.name = 'DashboardApiError'
        this.status = status
        this.code = code
        this.detail = detail || null
    }
}

export async function fetchJSON(path, params = {}) {
    const url = new URL(`${BASE}${path}`, window.location.origin)
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== '') {
            url.searchParams.set(key, value)
        }
    }

    const response = await fetch(url.toString(), { method: 'GET' })
    let payload = null
    try {
        payload = await response.json()
    } catch {
        payload = null
    }
    if (!response.ok) {
        throw new DashboardApiError(response.status, response.statusText, payload?.detail || payload)
    }
    return payload
}

export function formatApiError(error) {
    const detail = error?.detail || {}
    const action = detail?.primary_action || detail?.workflow?.primary_action || {}
    const code = detail?.code || error?.code || 'dashboard_request_failed'
    const actionText = action?.label || action?.command || ''
    return actionText ? `${code} · ${actionText}` : String(code)
}

export function fetchProjectInfo() {
    return fetchJSON('/api/project/info')
}

export function fetchWorkflow() {
    return fetchJSON('/api/canon-v3/workflow')
}

export function fetchCanonCharacters() {
    return fetchJSON('/api/canon-v3/characters')
}

export function fetchCanonObligations() {
    return fetchJSON('/api/canon-v3/obligations')
}

export function fetchCommits(params = {}) {
    return fetchJSON('/api/commits', params)
}

export function fetchFilesTree() {
    return fetchJSON('/api/files/tree')
}

export function fetchFileContent(path) {
    return fetchJSON('/api/files/read', { path })
}

export function subscribeSSE(onMessage, handlers = {}) {
    const { onOpen, onError } = handlers
    const eventSource = new EventSource(`${BASE}/api/events`)

    eventSource.onopen = () => {
        if (onOpen) onOpen()
    }

    eventSource.onmessage = event => {
        try {
            onMessage(JSON.parse(event.data))
        } catch { /* ignore non-JSON messages */ }
    }

    eventSource.onerror = error => {
        if (onError) onError(error)
    }

    return () => eventSource.close()
}
