import test from 'node:test';
import assert from 'node:assert/strict';
import { documentUploadGroups } from './documentUploads.js';

const files = (...names) => names.map((name) => ({ name }));

test('selected images form one document in selection order', () => {
  const selected = files('third.PNG', 'first.jpg', 'second.webp');
  assert.deepEqual(documentUploadGroups(selected), [selected]);
});

test('single image and PDFs retain separate uploads', () => {
  const selected = files('one.pdf', 'single.png', 'two.pdf');
  assert.deepEqual(documentUploadGroups(selected), selected.map((file) => [file]));
});

test('mixed selection groups only images, preserving other documents', () => {
  const selected = files('one.pdf', 'a.png', 'notes.txt', 'b.tiff', 'two.pdf');
  assert.deepEqual(documentUploadGroups(selected), [[selected[0]], [selected[1], selected[3]], [selected[2]], [selected[4]]]);
});

test('cancelled file selection produces no upload groups', () => {
  assert.deepEqual(documentUploadGroups([]), []);
});
