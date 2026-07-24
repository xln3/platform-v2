import { verifyWrongProductRole } from './product-role-isolation';

verifyWrongProductRole({
  product: 'Report Studio',
  path: '/platform/reports/?section=window',
  wrongRole: 'operator',
  protectedText: '冻结数据窗口',
});
