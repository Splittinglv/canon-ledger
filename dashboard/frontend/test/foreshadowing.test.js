import assert from 'node:assert/strict'
import test from 'node:test'

import {
    buildForeshadowingRecords,
    summarizeForeshadowing,
} from '../src/lib/foreshadowing.js'

test('open loops are derived from Canon obligations instead of project plot threads', () => {
    const records = buildForeshadowingRecords({
        latest_chapter: 3,
        plot_threads: {
            foreshadowing: [{ content: 'legacy-only row', status: 'active' }],
        },
        items: [
            {
                id: 'fact-open',
                category: 'open_loop_created',
                slot_id: 'loop-a',
                value: '谁拿走了钥匙？',
                payload: { kind: 'open_loop_created', slot_id: 'loop-a', loop: '谁拿走了钥匙？' },
                status: 'active',
                source_chapter: 2,
                fact_digest: 'a'.repeat(64),
            },
        ],
        lifecycle_history: [],
    })

    assert.deepEqual(records, [
        {
            id: 'loop-a',
            content: '谁拿走了钥匙？',
            plantedChapter: 2,
            resolvedChapter: 0,
            resolution: '',
            level: 'active',
            statusText: '活跃',
            factDigest: 'a'.repeat(64),
        },
    ])
})

test('lifecycle history joins a close event onto the stable open-loop slot', () => {
    const records = buildForeshadowingRecords({
        items: [],
        lifecycle_history: [
            {
                id: 'created',
                category: 'open_loop_created',
                slot_id: 'loop-b',
                payload: { kind: 'open_loop_created', slot_id: 'loop-b', loop: '暗门通往哪里？' },
                status: 'active',
                source_chapter: 1,
            },
            {
                id: 'closed',
                category: 'open_loop_closed',
                slot_id: 'loop-b',
                payload: { kind: 'open_loop_closed', slot_id: 'loop-b', resolution: '暗门通往地宫' },
                status: 'resolved',
                source_chapter: 4,
                fact_digest: 'b'.repeat(64),
            },
        ],
    })

    assert.equal(records.length, 1)
    assert.deepEqual(records[0], {
        id: 'loop-b',
        content: '暗门通往哪里？',
        plantedChapter: 1,
        resolvedChapter: 4,
        resolution: '暗门通往地宫',
        level: 'resolved',
        statusText: '已回收',
        factDigest: 'b'.repeat(64),
    })
    assert.deepEqual(summarizeForeshadowing(records), { total: 1, active: 0, resolved: 1 })
})

test('non-open-loop obligations are not presented as foreshadowing', () => {
    const records = buildForeshadowingRecords({
        items: [
            {
                id: 'promise-a',
                category: 'promise_created',
                payload: { kind: 'promise_created', promise: '三年后归还宝剑' },
                status: 'active',
                source_chapter: 2,
            },
        ],
        lifecycle_history: [],
    })

    assert.deepEqual(records, [])
})
