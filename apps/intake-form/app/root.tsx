import { Links, Meta, Outlet, Scripts, ScrollRestoration } from 'react-router';
import '@geo/design-system/styles.css';
import './intake.css';

export default function Root() {
  return (
    <html lang="zh-CN">
      <head>
        <title>GEO 客户信息收集表</title>
        <Meta />
        <Links />
        <link
          rel="icon"
          href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%2333517a'/%3E%3Cpath d='M18 20h28v8H26v8h18v8H18z' fill='white'/%3E%3C/svg%3E"
        />
      </head>
      <body>
        <Outlet />
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}
