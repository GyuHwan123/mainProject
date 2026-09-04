import { describe, expect, it } from 'vitest';

import { authErrorMessage, isValidEmail } from './authValidation';

describe('authentication form validation', () => {
  it.each(['user@example.com', 'first.last@company.co.kr'])('accepts a valid email: %s', (email) => {
    expect(isValidEmail(email)).toBe(true);
  });

  it.each(['user', 'user@', '@example.com', 'user @example.com', 'user@example'])('rejects an invalid email: %s', (email) => {
    expect(isValidEmail(email)).toBe(false);
  });

  it('turns backend email validation details into a friendly message', () => {
    const error = { response: { status: 422, data: { detail: [{ msg: 'value is not a valid email address' }] } } };
    expect(authErrorMessage(error)).toContain('올바른 이메일 형식');
  });
});
