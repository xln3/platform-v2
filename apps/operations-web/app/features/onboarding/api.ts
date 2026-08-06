import { createGeoApiClient, type IdentitySessionHeaders } from '@geo/api-client';

const API_BASE =
  (import.meta as ImportMeta & { env?: { VITE_GEO_API_BASE?: string } }).env?.VITE_GEO_API_BASE ??
  (typeof window === 'undefined' ? '' : window.location.origin);

export type SessionContext = {
  tenantId: string;
  actorId: string;
  role: 'operator' | 'reviewer' | 'admin';
  headers: IdentitySessionHeaders;
};

export const ONBOARDING_MODELS = ['doubao', 'deepseek', 'yiyan', 'tongyi', 'yuanbao'] as const;
export type OnboardingModel = (typeof ONBOARDING_MODELS)[number];

export const ONBOARDING_FREQUENCIES = ['one-off', 'daily', 'weekly', 'monthly'] as const;
export type OnboardingFrequency = (typeof ONBOARDING_FREQUENCIES)[number];

export type OnboardingCreateInput = {
  customerName: string;
  projectName: string;
  contactRole: string;
  audience: string;
  publicStatement: string;
  brandName: string;
  website: string;
  productName: string;
  competitors: string[];
  prohibitedClaim: string;
  goal: string;
  questions: string[];
  models: OnboardingModel[];
  regions: string[];
  frequency: OnboardingFrequency;
  truthConfirmed: boolean;
};

export type OnboardingView = {
  customer_pub_id: string;
  project_pub_id: string;
  config_version_pub_id: string;
  config_revision: number;
  task_count: number;
  mvp_document_url: string;
  measurement_requirements_url: string;
};

const client = createGeoApiClient(API_BASE);

function requireData<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data !== undefined) return result.data;
  const payload = result.error as
    | { error?: { code?: string }; detail?: { code?: string } }
    | undefined;
  throw new Error(
    payload?.error?.code ?? payload?.detail?.code ?? `http_${result.response.status}`,
  );
}

export const onboardingApi = {
  createOnboarding: async (
    session: SessionContext,
    input: OnboardingCreateInput,
  ): Promise<OnboardingView> =>
    requireData(
      await client.POST('/api/v2/onboarding', {
        params: {
          header: {
            ...session.headers,
            'Idempotency-Key': `onboarding-${crypto.randomUUID()}`,
          },
        },
        body: {
          customer_name: input.customerName,
          project_name: input.projectName,
          contact_role: input.contactRole,
          audience: input.audience,
          public_statement: input.publicStatement,
          brand_name: input.brandName,
          website: input.website,
          product_name: input.productName,
          competitors: input.competitors,
          prohibited_claim: input.prohibitedClaim,
          goal: input.goal,
          questions: input.questions,
          models: [...input.models],
          regions: input.regions,
          frequency: input.frequency,
          truth_confirmed: input.truthConfirmed,
        },
      }),
    ),
};
