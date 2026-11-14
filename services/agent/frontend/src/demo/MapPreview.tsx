import type { FieldContext } from "../widget";

// A stand-in for the team's MapLibre / Deck.gl map: draws the field outline and tints it for the
// active layer. The cell pattern is decorative and deterministic; it shows no data values.

const TINTS: Record<string, [string, string]> = {
  l_hls_ndvi: ["#1f5a3c", "#5fc28c"],
  l_smap_rootzone: ["#173f5c", "#6fb6de"],
  l_sm_30m: ["#4a3b1c", "#d7b56a"],
  l_nasadem_slope: ["#2a3438", "#a9b8b9"],
};

function hash(i: number, j: number): number {
  const x = Math.sin(i * 127.1 + j * 311.7) * 43758.5453;
  return x - Math.floor(x);
}

function mix(a: string, b: string, t: number): string {
  const pa = [1, 3, 5].map((k) => parseInt(a.slice(k, k + 2), 16));
  const pb = [1, 3, 5].map((k) => parseInt(b.slice(k, k + 2), 16));
  return `rgb(${pa.map((v, k) => Math.round(v + (pb[k]! - v) * t)).join(" ")})`;
}

export function MapPreview({ ctx, focusKey, labels }: { ctx: FieldContext | null; focusKey: number; labels: { illustrative: string; noField: string; point: string } }) {
  const W = 640;
  const H = 420;
  if (!ctx) {
    return (
      <div className="demo-map demo-map--empty">
        <p>{labels.noField}</p>
      </div>
    );
  }
  const [clon, clat] = ctx.centroid;
  const ring = ctx.geometry.type === "Polygon" ? ctx.geometry.coordinates[0]! : null;
  const span = ring ? Math.max(...ring.map(([x]) => Math.abs(x - clon)), ...ring.map(([, y]) => Math.abs(y - clat))) * 1.9 : 0.02;
  const k = Math.min(W, H) / 2 / span;
  const cosLat = Math.cos((clat * Math.PI) / 180);
  const px = (lon: number, lat: number) => [W / 2 + (lon - clon) * k * cosLat * 1.5, H / 2 - (lat - clat) * k] as const;
  const path = ring?.map(([x, y], n) => `${n ? "L" : "M"}${px(x, y).join(",")}`).join(" ") + "Z";
  const layer = ctx.ui_state?.active_layer_ids?.[0];
  const tint = layer ? TINTS[layer] : undefined;
  const cells = [];
  if (tint && ring) {
    const step = 14;
    for (let i = 0; i < W / step; i++)
      for (let j = 0; j < H / step; j++) {
        const v = (hash(i, j) + hash(Math.floor(i / 3), Math.floor(j / 3)) * 2) / 3;
        cells.push(<rect key={`${i}.${j}`} x={i * step} y={j * step} width={step} height={step} fill={mix(tint[0], tint[1], v)} />);
      }
  }
  return (
    <div className="demo-map" key={focusKey}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={labels.illustrative}>
        <defs>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M40 0H0V40" fill="none" stroke="rgb(168 232 201 / 0.06)" />
          </pattern>
          {path && (
            <clipPath id="field">
              <path d={path} />
            </clipPath>
          )}
        </defs>
        <rect width={W} height={H} fill="url(#grid)" />
        {ring ? (
          <>
            <g clipPath="url(#field)" opacity={0.85}>{cells}</g>
            <path d={path} fill={tint ? "none" : "rgb(54 166 125 / 0.12)"} stroke="#a8e8c9" strokeWidth={2} className="demo-field-outline" />
          </>
        ) : (
          <g transform={`translate(${W / 2} ${H / 2})`}>
            <circle r="28" fill="none" stroke="rgb(168 232 201 / 0.35)" />
            <circle r="6" fill="#a8e8c9" />
            <text y="52" textAnchor="middle" fill="#91aaa9" fontSize="13">{labels.point}</text>
          </g>
        )}
      </svg>
      <p className="demo-map-note">{labels.illustrative}</p>
    </div>
  );
}
