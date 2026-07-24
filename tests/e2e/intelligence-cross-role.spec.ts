import { verifyWrongProductRole } from './product-role-isolation';

verifyWrongProductRole({
  product: 'Intelligence Web',
  path: '/platform/intelligence/?section=claims',
  wrongRole: 'customer',
  protectedText: 'Claim 矩阵',
});
