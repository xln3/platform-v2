import { getHealth } from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { ProductShell, useOptionalExperienceContext } from '@geo/design-system';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import { QuotationGenerator } from './QuotationGenerator';

export default function QuotationGeneratorRoute() {
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const role = experience?.roles.find(
    (candidate): candidate is 'operator' | 'reviewer' | 'admin' =>
      candidate === 'operator' || candidate === 'reviewer' || candidate === 'admin',
  );
  return (
    <ProductShell
      product="Operations Web"
      title="报价单生成"
      description="品牌名称与目标词 XLSX 一键生成 GEO 服务报价单。"
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId="quotation-generator"
      probe={getHealth}
    >
      {() => (
        <QuotationGenerator
          session={
            experience && headers && role
              ? {
                  role,
                  headers,
                }
              : undefined
          }
        />
      )}
    </ProductShell>
  );
}
