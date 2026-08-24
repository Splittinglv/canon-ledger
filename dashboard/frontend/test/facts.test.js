import assert from 'node:assert/strict'
import test from 'node:test'

import { activeFactSummary, buildActiveFactRecords } from '../src/lib/facts.js'

test('active fact view merges genesis, author axioms and chapter facts only', () => {
    const rows = buildActiveFactRecords({
        items: [
            {
                authority_layer: 'active_canon',
                authority_state: 'active',
                origin: 'genesis',
                fact_digest: 'a'.repeat(64),
                category: 'world_rule',
                subject: 'initial_world',
                field: 'scale',
                value: '九州大陆',
                source_chapter: 0,
            },
            {
                authority_layer: 'active_canon',
                authority_state: 'active',
                origin: 'author_axiom',
                fact_digest: 'b'.repeat(64),
                category: 'world_rule',
                subject: 'author_axiom',
                field: 'death_is_irreversible',
                value: '死者不能复生',
                source_chapter: 0,
            },
            {
                authority_layer: 'active_canon',
                authority_state: 'active',
                origin: 'legacy_cutover',
                fact_digest: 'f'.repeat(64),
                category: 'relationship_changed',
                subject: '林舟',
                field: '苏月',
                value: '盟友',
                source_chapter: 2,
            },
            {
                authority_layer: 'active_canon',
                authority_state: 'active',
                origin: 'chapter_commit',
                fact_digest: 'c'.repeat(64),
                category: 'custody_changed',
                subject: '灵钥',
                field: 'custody',
                value: '林舟',
                source_chapter: 3,
            },
            {
                authority_layer: 'legacy_read_only',
                authority_state: 'active',
                origin: 'legacy',
                fact_digest: 'd'.repeat(64),
                value: '不能进入事实页',
            },
            {
                authority_layer: 'style',
                authority_state: 'active',
                origin: 'style',
                fact_digest: 'e'.repeat(64),
                value: '短句为主',
            },
        ],
    })

    assert.deepEqual(rows.map(row => row.value), ['九州大陆', '盟友', '死者不能复生', '林舟'])
    assert.deepEqual(activeFactSummary(rows), {
        total: 4,
        genesis: 2,
        authorAxioms: 1,
        chapters: 1,
    })
})
