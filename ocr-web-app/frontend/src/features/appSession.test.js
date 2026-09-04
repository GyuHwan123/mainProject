import { beforeEach, describe, expect, it, vi } from 'vitest';

import { clearAppSession } from './appSession';

function localStorageMock() {
  const values = new Map();
  return {
    getItem: vi.fn((key) => values.get(key) ?? null),
    setItem: vi.fn((key, value) => values.set(key, String(value))),
    removeItem: vi.fn((key) => values.delete(key)),
    clear: vi.fn(() => values.clear()),
  };
}

describe('app session logout cleanup', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', localStorageMock());
  });

  it('removes the Bearer JWT and cached user session values', () => {
    localStorage.setItem('pic_to_text_token', 'app-jwt');
    localStorage.setItem('pic_to_text_email', 'user@example.com');
    localStorage.setItem('pic_to_text_name', 'User');
    localStorage.setItem('pic_to_text_role', 'USER');
    localStorage.setItem('pic_to_text_subscription_tier', 'PERSONAL');
    localStorage.setItem('docunex_active_chat_session', 'chat-1');
    localStorage.setItem('docunex_chat_state:user@example.com', 'cached-chat');

    clearAppSession();

    expect(localStorage.getItem('pic_to_text_token')).toBeNull();
    expect(localStorage.getItem('pic_to_text_email')).toBeNull();
    expect(localStorage.getItem('pic_to_text_name')).toBeNull();
    expect(localStorage.getItem('pic_to_text_role')).toBeNull();
    expect(localStorage.getItem('pic_to_text_subscription_tier')).toBeNull();
    expect(localStorage.getItem('docunex_active_chat_session')).toBeNull();
    expect(localStorage.getItem('docunex_chat_state:user@example.com')).toBeNull();
  });
});
