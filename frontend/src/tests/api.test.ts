import { describe, expect, it } from 'vitest';
import { messageFrom } from '@/api/client';

/**
 * These bodies are copied verbatim from the running backend, so the test fails if the
 * error contract on either side drifts.
 */
const VALIDATION_422 = {
  code: 'validation_error',
  message: 'request failed validation',
  fields: [{ loc: ['body', 'password'], msg: 'String should have at least 12 characters' }],
};

describe('messageFrom', () => {
  it('shows the field detail, not the generic message, when a 422 carries both', () => {
    // The regression this guards: reading `message` first made `fields` unreachable, so
    // every rejection read "request failed validation" and the user could not act on it.
    const shown = messageFrom(422, VALIDATION_422);
    expect(shown).toBe('password: String should have at least 12 characters');
    expect(shown).not.toContain('request failed validation');
  });

  it('handles a 422 that carries fields but no message', () => {
    expect(
      messageFrom(422, {
        fields: [{ loc: ['body', 'username'], msg: "String should match pattern '[A-Za-z0-9._-]+'" }],
      }),
    ).toBe("username: String should match pattern '[A-Za-z0-9._-]+'");
  });

  it("strips Pydantic's internal 'Value error,' prefix", () => {
    expect(
      messageFrom(422, {
        message: 'request failed validation',
        fields: [{ loc: ['body', 'password'], msg: 'Value error, password is too common' }],
      }),
    ).toBe('password is too common');
  });

  it('does not stutter when the validator already names the field', () => {
    // "password: password is not varied enough" reads badly; the reason stands alone.
    expect(
      messageFrom(422, {
        fields: [{ loc: ['body', 'password'], msg: 'Value error, password is not varied enough' }],
      }),
    ).toBe('password is not varied enough');
  });

  it('still labels the field when the message does not name it', () => {
    expect(
      messageFrom(422, {
        fields: [{ loc: ['body', 'email'], msg: 'value is not a valid email address' }],
      }),
    ).toBe('email: value is not a valid email address');
  });

  it('joins every broken rule so one round trip fixes them all', () => {
    expect(
      messageFrom(422, {
        message: 'request failed validation',
        fields: [
          { loc: ['body', 'username'], msg: 'String should have at least 3 characters' },
          { loc: ['body', 'password'], msg: 'Value error, password is not varied enough' },
        ],
      }),
    ).toBe(
      'username: String should have at least 3 characters; password is not varied enough',
    );
  });

  it('strips the "body" prefix so the label matches the form field', () => {
    expect(messageFrom(422, { fields: [{ loc: ['body', 'email'], msg: 'not a valid email' }] })).toBe(
      'email: not a valid email',
    );
  });

  it('keeps handling a plain string detail — 401 and 409 still read correctly', () => {
    expect(messageFrom(401, 'invalid credentials')).toBe('invalid credentials');
    expect(messageFrom(409, 'email or username already registered')).toBe(
      'email or username already registered',
    );
  });

  it('falls back to the generic message when there are no field details', () => {
    expect(messageFrom(409, { code: 'conflict', message: 'already exists' })).toBe('already exists');
    expect(messageFrom(422, { message: 'request failed validation', fields: [] })).toBe(
      'request failed validation',
    );
  });

  it('never returns an empty string, whatever the payload', () => {
    for (const detail of [undefined, null, {}, [], 42, { fields: 'not-an-array' }]) {
      expect(messageFrom(500, detail).length).toBeGreaterThan(0);
    }
    expect(messageFrom(500, {})).toBe('request failed (500)');
  });

  it('survives a malformed field entry rather than throwing', () => {
    expect(messageFrom(422, { fields: [{}] })).toBe('field: invalid');
    expect(messageFrom(422, { fields: [{ loc: ['body'] }] })).toBe('field: invalid');
  });
});
