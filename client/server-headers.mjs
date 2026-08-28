const OMITTED_PROXY_RESPONSE_HEADERS = new Set([
  "connection",
  "content-encoding",
  "transfer-encoding",
]);

export function collectProxyResponseHeaders(headers) {
  const result = {};

  headers.forEach((value, key) => {
    const lowered = key.toLowerCase();
    if (
      lowered === "set-cookie" ||
      OMITTED_PROXY_RESPONSE_HEADERS.has(lowered)
    ) {
      return;
    }
    result[key] = value;
  });

  const setCookies = headers.getSetCookie();
  if (setCookies.length > 0) {
    result["set-cookie"] = setCookies;
  }

  return result;
}
