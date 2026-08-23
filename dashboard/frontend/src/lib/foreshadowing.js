const OPEN_LOOP_KINDS = new Set(['open_loop', 'open_loop_created', 'open_loop_closed'])

function toNonNegativeInt(value) {
    const number = Number.parseInt(String(value ?? ''), 10)
    return Number.isFinite(number) && number >= 0 ? number : 0
}

function payloadOf(row) {
    return row?.payload && typeof row.payload === 'object' ? row.payload : {}
}

function categoryOf(row) {
    const payload = payloadOf(row)
    return String(row?.category || payload.kind || '')
}

function lifecycleId(row) {
    const payload = payloadOf(row)
    return String(
        row?.slot_id
        || payload.slot_id
        || payload.lifecycle_id
        || payload.loop_id
        || row?.id
        || row?.fact_digest
        || '',
    )
}

function contentOf(row) {
    const payload = payloadOf(row)
    return String(
        payload.loop
        || payload.content
        || row?.value
        || row?.subject
        || '未命名开放问题',
    )
}

export function isOpenLoopRow(row) {
    return OPEN_LOOP_KINDS.has(categoryOf(row))
}

export function buildForeshadowingRecords(canonPayload) {
    const activeRows = Array.isArray(canonPayload?.items) ? canonPayload.items : []
    const historyRows = Array.isArray(canonPayload?.lifecycle_history)
        ? canonPayload.lifecycle_history
        : []
    const rows = [...historyRows]
        .filter(isOpenLoopRow)
        .sort((left, right) => {
            const chapterOrder = toNonNegativeInt(left?.source_chapter) - toNonNegativeInt(right?.source_chapter)
            if (chapterOrder !== 0) return chapterOrder
            return toNonNegativeInt(left?.revision) - toNonNegativeInt(right?.revision)
        })

    const records = new Map()
    for (const row of rows) {
        const id = lifecycleId(row)
        if (!id) continue
        const payload = payloadOf(row)
        const category = categoryOf(row)
        const current = records.get(id) || {
            id,
            content: contentOf(row),
            plantedChapter: 0,
            resolvedChapter: 0,
            resolution: '',
            level: 'active',
            statusText: '活跃',
            factDigest: '',
        }
        if (!current.plantedChapter && category !== 'open_loop_closed') {
            current.plantedChapter = toNonNegativeInt(row?.source_chapter)
        }
        if (category === 'open_loop_closed' || row?.status === 'resolved') {
            current.level = 'resolved'
            current.statusText = '已回收'
            current.resolvedChapter = toNonNegativeInt(row?.source_chapter)
            current.resolution = String(payload.resolution || row?.value || '')
        } else {
            current.content = contentOf(row)
        }
        current.factDigest = String(row?.fact_digest || current.factDigest || '')
        records.set(id, current)
    }

    const activeIds = new Set()
    for (const row of activeRows.filter(isOpenLoopRow)) {
        const id = lifecycleId(row)
        if (!id) continue
        activeIds.add(id)
        const current = records.get(id) || {
            id,
            plantedChapter: toNonNegativeInt(row?.source_chapter),
            resolvedChapter: 0,
            resolution: '',
        }
        records.set(id, {
            ...current,
            content: contentOf(row),
            level: 'active',
            statusText: '活跃',
            factDigest: String(row?.fact_digest || current.factDigest || ''),
        })
    }

    for (const [id, record] of records.entries()) {
        if (!activeIds.has(id) && record.level !== 'resolved') {
            record.level = 'resolved'
            record.statusText = '已回收'
        }
    }

    return [...records.values()].sort((left, right) => {
        if (left.level !== right.level) return left.level === 'active' ? -1 : 1
        return (right.plantedChapter || 0) - (left.plantedChapter || 0)
    })
}

export function summarizeForeshadowing(records) {
    return records.reduce(
        (summary, record) => {
            summary.total += 1
            if (record.level === 'resolved') summary.resolved += 1
            else summary.active += 1
            return summary
        },
        { total: 0, active: 0, resolved: 0 },
    )
}
