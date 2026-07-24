import { verifyWrongProductRole } from './product-role-isolation';

verifyWrongProductRole({
  product: 'Operations Web',
  path: '/platform/operations/?section=sessions',
  wrongRole: 'customer',
  protectedText: '会话健康',
});
