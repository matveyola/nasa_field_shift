/** Hosts whose pages may be linked from a citation: the knowledge base's sources (agent_service/knowledge/sources.py):
 *  FAO-56, and the two NAAS recommendation PDFs (Cherkasy station 2024, Polissia institute 2026). */
export const DEFAULT_TRUSTED_SOURCE_HOSTS = ["www.fao.org", "fao.org", "agrobiz.net", "www.isgpnaan.org"] as const;

/**
 * The href to render for a citation URL, or null. Only https, only allowlisted hosts (exact match),
 * no credentials in the URL. Citation URLs already come from the service's corpus metadata; this
 * is the browser-side second check, so even a compromised response cannot link elsewhere.
 */
export function safeSourceUrl(raw: string, trustedHosts: readonly string[]): string | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.username || url.password) return null;
  if (!trustedHosts.includes(url.hostname.toLowerCase())) return null;
  return url.href;
}
