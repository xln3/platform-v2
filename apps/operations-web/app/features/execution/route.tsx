import { ExecutionControlPlane } from './ExecutionControlPlane';

export default function ExecutionRoute() {
  const tenantId =
    typeof window === 'undefined' ? '' : (window.localStorage.getItem('geo.ops.tenant') ?? '');
  const actorId =
    typeof window === 'undefined' ? '' : (window.localStorage.getItem('geo.ops.actor') ?? '');
  const storedRole =
    typeof window === 'undefined'
      ? 'operator'
      : (window.localStorage.getItem('geo.ops.role') ?? 'operator');
  const role = storedRole === 'admin' || storedRole === 'reviewer' ? storedRole : 'operator';
  if (!tenantId || !actorId) {
    return (
      <main className="execution-plane">
        <div className="execution-state warning">
          尚未建立运营会话。请通过身份入口登录；页面不会把账号秘密写入浏览器存储。
        </div>
      </main>
    );
  }
  return <ExecutionControlPlane session={{ tenantId, actorId, role }} />;
}
