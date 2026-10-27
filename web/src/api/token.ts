// The access token comes from the link printed by `simubench serve` (?token=...). It is kept for the
// browser tab and removed from the address bar so that it is not shared by copying the URL.
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
