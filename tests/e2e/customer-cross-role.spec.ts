import { verifyWrongProductRole } from './product-role-isolation';

verifyWrongProductRole({
  product: 'Customer Web',
  path: '/platform/customer/?section=accounts',
  wrongRole: 'operator',
  protectedText: '平台账号与授权',
});
