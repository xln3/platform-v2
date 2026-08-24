const productionFrontendPorts = new Map([
  ['/platform/customer/', '8787'],
  ['/platform/operations/', '8788'],
]);

export function productionFrontendURL(baseURL, route, isolatedCandidate = false) {
  const target = new URL(route, baseURL);
  if (isolatedCandidate) return target.toString();

  for (const [prefix, port] of productionFrontendPorts) {
    if (target.pathname.startsWith(prefix)) {
      target.port = port;
      break;
    }
  }
  return target.toString();
}
