import { verifyWrongProductRole } from './product-role-isolation';

verifyWrongProductRole({
  product: 'Operations Web',
  path: '/platform/operations/?section=overview',
  wrongRole: 'customer',
  protectedText: '项目组合',
  // 运营端错误角色的落点是统一登录页（20260810 登录页去内部化重构后的现行行为）。
  forbiddenText: 'GEO 平台登录',
});
