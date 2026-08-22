import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AvailabilityBadge, CATEGORY_LABEL, SeverityBadge, TokenStateBadge } from '@/selfosint/badges';
import type { Availability, ExposureFinding, TokenState } from '@/api/types';

/**
 * The property under test is the feature's honesty contract: a section with no data must
 * be distinguishable from a capability the official API does not offer. If these labels
 * collapse into one another the UI starts implying "we checked and found nothing" where
 * the truth is "Meta never exposes this".
 */
describe('availability badges', () => {
  const cases: [Availability, string][] = [
    ['available', 'AVAILABLE'],
    ['not_available', 'NOT AVAILABLE'],
    ['requires_permission', 'REQUIRES PERMISSION'],
    ['not_exposed', 'NOT EXPOSED'],
    ['external_source', 'EXTERNAL SOURCE'],
  ];

  it.each(cases)('renders %s as its own distinct label', (value, label) => {
    expect(renderToStaticMarkup(<AvailabilityBadge value={value} />)).toContain(label);
  });

  it('gives every state a distinct label', () => {
    const labels = cases.map(([value]) =>
      renderToStaticMarkup(<AvailabilityBadge value={value} />),
    );
    expect(new Set(labels).size).toBe(cases.length);
  });

  it('carries the meaning in text, not colour alone', () => {
    // Colour-only encoding is unreadable for a large minority of users and invisible to
    // anyone reading a printed or exported report.
    const markup = renderToStaticMarkup(<AvailabilityBadge value="not_available" />);
    expect(markup).toContain('NOT AVAILABLE');
    expect(markup).toContain('does not expose this');
  });

  it('explains that unavailable data is not scraped around', () => {
    const markup = renderToStaticMarkup(<AvailabilityBadge value="not_available" />);
    expect(markup).toContain('We do not scrape it.');
  });
});

describe('severity badges', () => {
  it.each(['high', 'medium', 'low', 'info'] as const)('renders %s', (severity) => {
    expect(renderToStaticMarkup(<SeverityBadge value={severity} />)).toContain(severity);
  });
});

describe('token state badges', () => {
  const states: TokenState[] = [
    'active',
    'expired',
    'revoked',
    'invalid',
    'reauthorization_required',
  ];

  it('labels every lifecycle state the backend can return', () => {
    const rendered = states.map((state) =>
      renderToStaticMarkup(<TokenStateBadge value={state} />),
    );
    expect(new Set(rendered).size).toBe(states.length);
    expect(rendered.every((markup) => markup.length > 0)).toBe(true);
  });

  it('tells the user to reconnect rather than showing a raw enum', () => {
    const markup = renderToStaticMarkup(<TokenStateBadge value="reauthorization_required" />);
    expect(markup).toContain('Reconnection required');
    expect(markup).not.toContain('reauthorization_required');
  });
});

describe('category labels', () => {
  it('has plain-language wording for every backend category', () => {
    // Mirrors app/core/enums.py::ExposureCategory. A missing entry would render a raw
    // snake_case key to a non-technical reader.
    const backendCategories = [
      'identity_exposure',
      'username_reuse',
      'contact_exposure',
      'location_exposure',
      'organization_exposure',
      'external_account_linkage',
      'public_media_exposure',
      'metadata_exposure',
      'external_mentions',
    ];
    for (const category of backendCategories) {
      const label = CATEGORY_LABEL[category];
      expect(label, `missing label for ${category}`).toBeTruthy();
      expect(label ?? '').not.toContain('_');
    }
  });
});

describe('finding shape', () => {
  it('models every field the report contract promises', () => {
    // A compile-time guard: if the backend contract grows a required field, this stops
    // type-checking rather than silently rendering undefined.
    const finding: ExposureFinding = {
      category: 'username_reuse',
      severity: 'high',
      title: 'Username reused across multiple platforms',
      description: 'The same username appears across three services.',
      mitigation: 'Use a distinct handle where you do not want accounts linked.',
      confidence: 0.9,
      availability: 'available',
      evidence: [
        { label: 'Instagram @example', source: 'instagram_self', url: null, entity_id: null, observation_id: null },
      ],
    };
    expect(finding.evidence[0]?.source).toBe('instagram_self');
    expect(finding.confidence).toBeLessThanOrEqual(1);
  });
});
