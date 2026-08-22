import clsx from 'clsx';
import type { Availability, ExposureSeverity, TokenState } from '@/api/types';

/**
 * Availability badges.
 *
 * The distinction these carry is the point of the whole feature: "we found nothing" and
 * "the API does not offer this" look identical in a UI that just renders an empty panel,
 * and conflating them tells the user something false about their own exposure. Each
 * badge pairs a colour with a *word*, never colour alone.
 */
const AVAILABILITY_LABEL: Record<Availability, string> = {
  available: 'AVAILABLE',
  not_available: 'NOT AVAILABLE',
  requires_permission: 'REQUIRES PERMISSION',
  not_exposed: 'NOT EXPOSED',
  external_source: 'EXTERNAL SOURCE',
};

const AVAILABILITY_STYLE: Record<Availability, string> = {
  available: 'border-observed/60 text-observed',
  not_available: 'border-line text-fg-dim',
  requires_permission: 'border-inferred/60 text-inferred',
  not_exposed: 'border-line text-fg-muted',
  external_source: 'border-correlated/60 text-correlated',
};

const AVAILABILITY_HELP: Record<Availability, string> = {
  available: 'Instagram returned this for your account.',
  not_available: 'The official API does not expose this to anyone. We do not scrape it.',
  requires_permission: 'The API offers this, but you did not grant the permission.',
  not_exposed: 'Available through the API, but your account has nothing here.',
  external_source: 'Not from Instagram — found by public-source providers.',
};

export function AvailabilityBadge({ value }: { value: Availability }) {
  return (
    <span
      title={AVAILABILITY_HELP[value]}
      className={clsx(
        'inline-block whitespace-nowrap rounded-full border px-1.5 py-px text-2xs font-medium',
        AVAILABILITY_STYLE[value],
      )}
    >
      {AVAILABILITY_LABEL[value]}
    </span>
  );
}

const SEVERITY_STYLE: Record<ExposureSeverity, string> = {
  high: 'border-danger/60 bg-danger/10 text-danger',
  medium: 'border-inferred/60 bg-inferred/10 text-inferred',
  low: 'border-correlated/50 bg-correlated/10 text-correlated',
  info: 'border-line text-fg-muted',
};

export function SeverityBadge({ value }: { value: ExposureSeverity }) {
  return (
    <span
      className={clsx(
        'inline-block rounded border px-1.5 py-px text-2xs font-semibold uppercase',
        SEVERITY_STYLE[value],
      )}
    >
      {value}
    </span>
  );
}

const TOKEN_LABEL: Record<TokenState, string> = {
  active: 'Connected',
  expired: 'Authorisation expired',
  revoked: 'Disconnected',
  invalid: 'Credential unusable',
  reauthorization_required: 'Reconnection required',
};

const TOKEN_STYLE: Record<TokenState, string> = {
  active: 'border-observed/60 text-observed',
  expired: 'border-inferred/60 text-inferred',
  revoked: 'border-line text-fg-dim',
  invalid: 'border-danger/60 text-danger',
  reauthorization_required: 'border-inferred/60 text-inferred',
};

export function TokenStateBadge({ value }: { value: TokenState }) {
  return (
    <span
      className={clsx(
        'inline-block rounded-full border px-2 py-px text-2xs',
        TOKEN_STYLE[value],
      )}
    >
      {TOKEN_LABEL[value]}
    </span>
  );
}

/** Human wording for the report's category keys. */
export const CATEGORY_LABEL: Record<string, string> = {
  identity_exposure: 'Identity exposure',
  username_reuse: 'Username reuse',
  contact_exposure: 'Contact exposure',
  location_exposure: 'Location exposure',
  organization_exposure: 'Organisation exposure',
  external_account_linkage: 'External account linkage',
  public_media_exposure: 'Public media exposure',
  metadata_exposure: 'Metadata exposure',
  external_mentions: 'External mentions',
};
