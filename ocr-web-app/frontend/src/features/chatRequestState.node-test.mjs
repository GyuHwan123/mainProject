import test from 'node:test';
import assert from 'node:assert/strict';
import { completedChatMessages } from './chatRequestState.mjs';

const user = (text, status) => ({ role: 'user', text, status });
const assistant = (text) => ({ role: 'assistant', text });

test('refresh preserves completed exchanges and removes an in-flight question', () => {
  const exchange = [user('first'), assistant('answer')];
  assert.deepEqual(completedChatMessages([...exchange, user('second', 'pending')]), exchange);
});

test('legacy orphan questions are discarded without pairing with the next answer', () => {
  const exchange = [user('new question'), assistant('new answer')];
  assert.deepEqual(completedChatMessages([user('interrupted'), ...exchange, user('orphan')]), exchange);
});

test('empty and interrupted-only histories show a greeting, never a waiting question', () => {
  for (const messages of [[], [user('pending', 'pending')], [user('legacy')]]) {
    const restored = completedChatMessages(messages);
    assert.equal(restored.length, 1);
    assert.equal(restored[0].role, 'assistant');
  }
});

test('DB message content and evidence are preserved', () => {
  const messages = [{ role: 'user', content: 'question' }, { role: 'assistant', content: 'answer', sources: [{ id: 1 }] }];
  assert.deepEqual(completedChatMessages(messages), messages);
});
