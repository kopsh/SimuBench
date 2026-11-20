// The access token is opt-in (`simubench serve --require-token`, D43); when on, it comes from the
// printed link (?token=...). It is kept for the browser tab and removed from the address bar so that
// it is not shared by copying the URL. Without it, requests simply carry no token.
//
// How the token travels — the one statement of it (D43, #1735):
//   * HTTP requests carry it in the `x-simubench-token` header (client.ts's auth middleware,
//     stream.ts's assistant event fetch).
//   * The two WebSocket streams carry it in the URL's `token` query parameter (wsTransport.ts's
//     wsUrl) — the one exception to "not in a URL": a browser WebSocket cannot set custom
//     headers, and both streams connect to this same origin, which the server's Host/Origin
//     check already pins (docs/security.md).
// A caller reads the token only through getToken below; how to present it is not decided again
// anywhere.
let token: string | null = null;

export function getToken(): string | null {
  if (token) return token;
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    token = fromUrl;
    try {
      window.sessionStorage.setItem("simubench:token", fromUrl);
    } catch {
      /* keep it in memory only */
    }
    url.searchParams.delete("token");
    window.history.replaceState(window.history.state, "", url.toString());
    return token;
  }
  try {
    token = window.sessionStorage.getItem("simubench:token");
  } catch {
    token = null;
  }
  return token;
}
