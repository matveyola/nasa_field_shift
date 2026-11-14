import assert from 'node:assert/strict'
import test from 'node:test'
import { markNotificationsRead, parseReadNotifications } from '../src/features/notifications/notificationReadState.ts'

test('invalid or corrupted saved read state cannot break the notification UI', () => {
  for (const raw of [null, '', 'broken', '{}', 'null', '3']) assert.deepEqual(parseReadNotifications(raw), [])
  assert.deepEqual(parseReadNotifications('["water:field1",0,null,"","water:field1","ndvi:field2"]'), ['water:field1', 'ndvi:field2'])
  assert.deepEqual(parseReadNotifications(JSON.stringify(['x'.repeat(1001)])), [])
})

test('reading signals retains other read markers without mutation and bounds local history', () => {
  const previous = ['one', 'two']
  assert.deepEqual(markNotificationsRead(previous, ['two', 'three', 'three']), ['one', 'two', 'three'])
  assert.deepEqual(previous, ['one', 'two'])
  const ids = Array.from({ length: 600 }, (_, index) => String(index))
  assert.deepEqual(parseReadNotifications(JSON.stringify(ids)), ids.slice(100))
  assert.deepEqual(markNotificationsRead(ids, ['new']), [...ids.slice(101), 'new'])
})
