const ORIGIN_LABELS = {
    genesis: '初始化事实',
    author_axiom: '作者长期硬设定',
    chapter_commit: '已发布章节',
}

const ORIGIN_ORDER = { genesis: 0, author_axiom: 1, chapter_commit: 2 }

function text(value) {
    return String(value ?? '').trim()
}

export function buildActiveFactRecords(payload = {}) {
    const rows = Array.isArray(payload?.items) ? payload.items : []
    return rows
        .filter(row => (
            row
            && row.authority_layer === 'active_canon'
            && row.authority_state === 'active'
            && text(row.fact_digest)
        ))
        .map(row => ({
            id: text(row.fact_digest),
            digest: text(row.fact_digest),
            origin: text(row.origin),
            originLabel: ORIGIN_LABELS[text(row.origin)] || text(row.origin) || 'Canon HEAD',
            category: text(row.category) || text(row?.payload?.kind) || 'story_fact',
            subject: text(row.subject) || '—',
            field: text(row.field) || '—',
            value: row.value ?? row?.payload?.after ?? row?.payload?.rule ?? '',
            chapter: Number(row.source_chapter) || 0,
            status: text(row.status) || 'active',
        }))
        .sort((left, right) => (
            (ORIGIN_ORDER[left.origin] ?? 9) - (ORIGIN_ORDER[right.origin] ?? 9)
            || left.chapter - right.chapter
            || left.category.localeCompare(right.category)
            || left.subject.localeCompare(right.subject)
            || left.field.localeCompare(right.field)
            || left.digest.localeCompare(right.digest)
        ))
}

export function activeFactSummary(rows = []) {
    const result = { total: rows.length, genesis: 0, authorAxioms: 0, chapters: 0 }
    for (const row of rows) {
        if (row.origin === 'genesis') result.genesis += 1
        else if (row.origin === 'author_axiom') result.authorAxioms += 1
        else if (row.origin === 'chapter_commit') result.chapters += 1
    }
    return result
}
