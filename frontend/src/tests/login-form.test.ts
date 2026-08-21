import { describe, expect, it } from 'vitest';
import { USERNAME_PATTERN, PASSWORD_MIN_LENGTH, USERNAME_MIN_LENGTH } from '@/components/Login';

/**
 * The `pattern` attribute is compiled by the browser with the RegExp `v` flag, and an
 * uncompilable pattern is *silently ignored* — the field ends up with no constraint and
 * the failure is invisible until a real browser rejects nothing. This locks that down.
 */
describe('register form constraints', () => {
  it('the username pattern compiles the way a browser compiles it', () => {
    expect(() => new RegExp(`^(?:${USERNAME_PATTERN})$`, 'v')).not.toThrow();
  });

  const anchored = () => new RegExp(`^(?:${USERNAME_PATTERN})$`, 'v');

  it.each(['analyst', 'an.aly-st_1', 'ABC123', 'a-b', 'a_b', 'a.b'])(
    'accepts the valid username %s',
    (value) => expect(anchored().test(value)).toBe(true),
  );

  it.each(['my user', 'a@example.com', 'user!', 'يوزر', 'user/name', ''])(
    'rejects the invalid username %s',
    (value) => expect(anchored().test(value)).toBe(false),
  );

  it('mirrors the limits the server enforces in RegisterRequest', () => {
    // If these drift from backend/app/schemas/auth.py the form starts lying again.
    expect(USERNAME_MIN_LENGTH).toBe(3);
    expect(PASSWORD_MIN_LENGTH).toBe(12);
  });
});
