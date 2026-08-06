import { useEffect, useRef } from 'react';
import type { IdentitySessionHeaders } from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { createStructuredClientScopeKey } from '@geo/design-system';

export type CustomerAccountMutationTicket = Readonly<{
  context: string;
  generation: number;
  identity: string;
}>;

const fixtureIdentity = 'contract-fixture';
const identityContext = (headers: IdentitySessionHeaders | null): string => {
  const tenant = headers?.['X-Tenant-Id'];
  const actor = headers?.['X-Actor-Id'];
  const role = headers?.['X-Actor-Role'];
  return tenant && actor && role ? createStructuredClientScopeKey([tenant, actor, role]) : '';
};

export function useCustomerMutationGuard(context: string) {
  const active = useRef(false);
  const generation = useRef(0);
  const contextRef = useRef(context);
  if (contextRef.current !== context) {
    contextRef.current = context;
    generation.current += 1;
    active.current = false;
  }
  useEffect(
    () => () => {
      generation.current += 1;
      active.current = false;
    },
    [],
  );
  return {
    begin(headers: IdentitySessionHeaders): CustomerAccountMutationTicket | null {
      if (active.current) return null;
      const identity = identityContext(headers);
      if (!identity) return null;
      active.current = true;
      generation.current += 1;
      return { context: contextRef.current, generation: generation.current, identity };
    },
    beginFixture(): CustomerAccountMutationTicket | null {
      if (active.current) return null;
      active.current = true;
      generation.current += 1;
      return {
        context: contextRef.current,
        generation: generation.current,
        identity: fixtureIdentity,
      };
    },
    isCurrent(ticket: CustomerAccountMutationTicket): boolean {
      return (
        active.current &&
        ticket.context === contextRef.current &&
        ticket.generation === generation.current &&
        (ticket.identity === fixtureIdentity ||
          ticket.identity === identityContext(getValidatedIdentityHeaders()))
      );
    },
    finish(ticket: CustomerAccountMutationTicket): boolean {
      if (
        ticket.context !== contextRef.current ||
        ticket.generation !== generation.current ||
        !active.current
      ) {
        return false;
      }
      active.current = false;
      return (
        ticket.identity === fixtureIdentity ||
        ticket.identity === identityContext(getValidatedIdentityHeaders())
      );
    },
    isActive(): boolean {
      return active.current;
    },
  };
}

export function useCustomerAccountMutationGuard(context: string) {
  return useCustomerMutationGuard(context);
}
