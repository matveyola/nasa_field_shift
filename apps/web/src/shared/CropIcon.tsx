import { cropIconKind, type CropKind } from './cropIconKind'

const drawings: Record<CropKind, () => React.JSX.Element> = {
  wheat: Wheat, maize: Maize, soy: Soy, barley: Barley, sunflower: Sunflower,
  rapeseed: Rapeseed, peas: Peas, sugar_beet: SugarBeet, potato: Potato, oats: Oats, millet: Millet,
}

export function CropIcon({ crop }: { crop?: string }) {
  const kind = cropIconKind(crop)
  const Drawing = kind ? drawings[kind] : GenericCrop
  return (
    <svg className="crop-icon" viewBox="0 0 32 32" width="24" height="24"
      aria-hidden="true" focusable="false" strokeLinejoin="round" strokeLinecap="round">
      <Drawing />
    </svg>
  )
}

function GenericCrop() {
  return (
    <g fill="#d5dfc0" stroke="#59734b" strokeWidth="1.4">
      <path d="M16 26c0-7 .3-10 3-13M16 20c-1.2-5-5-6.8-9-7 .4 5 3.4 7.7 9 7Z" />
      <path d="M17 17c.2-5 3-8 8-9-.1 5-3 8.2-8 9Z" />
    </g>
  )
}

function Wheat() {
  return (
    <g transform="rotate(39 16 16)" stroke="#a16a22" strokeWidth=".85">
      <path d="M15.8 31C15.9 22.8 16.2 13.7 16 4.3" strokeWidth="1.35" fill="none" />
      <path d="M16 9.7c-2.7-2.5-2.2-5.6 0-8 2.1 2.6 2.8 5.3 0 8Z" fill="#dfad46" />
      <path d="M15.7 14c-4.5-.5-6.1-2.7-6.7-6.4 4.1 1.3 6.2 3.6 6.7 6.4Z" fill="#e0a840" />
      <path d="M16.4 13.9c4.4-.5 6.1-2.7 6.7-6.4-4 1.3-6.1 3.6-6.7 6.4Z" fill="#f0c363" />
      <path d="M15.6 19.1c-4.5-.5-6.3-2.9-7.1-6.4 4.4 1.2 6.5 3.4 7.1 6.4Z" fill="#dda241" />
      <path d="M16.5 19.1c4.4-.6 6.2-3 7-6.5-4.3 1.3-6.4 3.6-7 6.5Z" fill="#eeb959" />
      <path d="M15.6 24.2c-4.6-.1-6.6-2.4-7.5-5.7 4.4.8 6.6 2.7 7.5 5.7Z" fill="#ce8c32" />
      <path d="M16.4 24.2c4.6-.2 6.4-2.4 7.4-5.7-4.4.8-6.5 2.7-7.4 5.7Z" fill="#e5ad46" />
    </g>
  )
}

function Maize() {
  return (
    <g transform="rotate(31 16 16)">
      <path d="M15.9 3c3.1 0 5.3 3.1 5.6 7.3l.6 9.4c.1 4-2.6 6.4-6.1 6.4-3.5 0-6.2-2.5-6.1-6.4l.6-9.4c.3-4.2 2.5-7.3 5.4-7.3Z"
        fill="#eec158" stroke="#9c742e" strokeWidth="1" />
      <path d="M14.2 5.2c-1.6 1.5-2.2 3.5-2.3 6.3l-.3 5.4" fill="none" stroke="#fae09b" strokeWidth="1.2" />
      <path d="M16.2 7v13.6M18.9 8.5l.4 11.3M13.2 8.8l-.5 10.8M11.7 11.2h8.4m-8.5 3.3h8.7m-8.9 3.3h9.2"
        fill="none" stroke="#b98735" strokeWidth=".7" opacity=".86" />
      <path d="M16 29.4C8.4 27.3 5.7 21.5 6.2 14.2c3.1 5.1 7.9 5.4 10.2 13.8Z"
        fill="#7c983e" stroke="#466330" strokeWidth="1" />
      <path d="M16 29.4c-.6-6.9 4.1-8.6 9.9-15.3-.2 7.9-3.2 12.9-9.9 15.3Z"
        fill="#557636" stroke="#37582e" strokeWidth="1" />
      <path d="M8.1 18.8c1.3 4.1 3.7 6.4 6.5 8.2m9.2-8.6c-1.7 3.7-3.5 6.1-6 8.4"
        fill="none" stroke="#b1bd69" strokeWidth=".8" />
    </g>
  )
}

function Soy() {
  return (
    <g strokeWidth="1" stroke="#476333">
      <path d="M10.8 17.8c-.6-3.5 1.7-5.7 5.1-6.3.8-2.5 3.2-4 5.6-3.5 1.7-1.9 4.5-2.4 6.3-1.4.2 2.5-1.8 4.7-3.8 5.7-.6 2.7-2.8 4.2-5.2 4.1-2 2.5-5.7 3.4-8 1.4Z"
        fill="#8da54f" />
      <path d="M27.4 7.2c1.1-.6 1.7-1.4 2.1-2.5M12.3 17.1c4.5-1.5 9.9-5.7 13.8-9.2"
        fill="none" />
      <path d="M2.6 23.3c1.3-2.4 4.1-3.6 7-3.3 1.4-2.8 4-3.6 6.4-3.2 1.6-2.6 4.5-3.4 7.6-2.4.6 3.1-1.7 5.5-4.5 6.4-1.4 2.6-3.9 3.7-6.4 3.3-2.8 2.4-7.3 2.6-10.1-.8Z"
        fill="#69853d" />
      <path d="M3.9 23.3c5.4.4 12.5-2.6 18.2-7.7" fill="none" stroke="#b5c679" strokeWidth="1.15" />
      <ellipse cx="8.1" cy="22.6" rx="1.8" ry=".95" fill="#b6c96d" stroke="none" />
      <ellipse cx="14" cy="20.2" rx="1.8" ry="1" transform="rotate(-28 14 20.2)" fill="#afc365" stroke="none" />
      <ellipse cx="19.3" cy="17.3" rx="1.55" ry=".8" transform="rotate(-36 19.3 17.3)" fill="#bace72" stroke="none" />
    </g>
  )
}

function Barley() {
  return (
    <g transform="translate(2 3) scale(.85) rotate(23 16 16)" stroke="#9b7732" strokeWidth=".8">
      <path d="M16 30V6M13 15 8 3m7 8L13 1m4 10L19 1m0 14 5-12M11 20 6 7m15 13 5-13" fill="none" />
      <path d="M16 12c-3-1-4-3-4-6 3 1 4 3 4 6Zm0 5c-4-1-5-4-5-7 4 2 5 4 5 7Zm0 5c-4-1-6-4-6-7 4 1 6 4 6 7Z" fill="#ddb35d" />
      <path d="M16 12c3-1 4-3 4-6-3 1-4 3-4 6Zm0 5c4-1 5-4 5-7-4 2-5 4-5 7Zm0 5c4-1 6-4 6-7-4 1-6 4-6 7Z" fill="#edca7a" />
      <path d="M16 29c-5-2-7-4-8-7 4 1 6 3 8 7Z" fill="#9b9d52" />
    </g>
  )
}

function Sunflower() {
  return (
    <g strokeLinejoin="round">
      <path d="M16 30V13" stroke="#4e6e36" strokeWidth="1.5" />
      <path d="M16 26c-6 0-9-3-10-7 5 0 9 2 10 7Zm0-3c1-5 5-7 10-7-1 5-5 7-10 7Z" fill="#77934b" stroke="#4e6e36" strokeWidth="1" />
      <g fill="#edbd47" stroke="#aa7628" strokeWidth=".7">
        {Array.from({ length: 10 }, (_, i) => <path key={i} transform={`rotate(${i * 36} 16 11)`} d="M14 7c-1-3 0-5 2-6 2 1 3 3 2 6Z" />)}
      </g>
      <circle cx="16" cy="11" r="5.5" fill="#6c4c31" stroke="#493b2b" strokeWidth=".8" />
      <path d="m13 8 6 6m-7-3 4 4m0-8 4 4m-7 3 6-6m-7 3 4-4m0 8 4-4" stroke="#bc965b" strokeWidth=".65" />
    </g>
  )
}

function Rapeseed() {
  return (
    <g stroke="#58753a" strokeWidth="1">
      <path d="M16 30V9m0 12L8 13m8 4 8-9" fill="none" strokeWidth="1.3" />
      <path d="M16 28c-6-1-8-4-8-7 5 1 7 3 8 7Zm0-4c2-4 5-5 9-5-1 4-4 5-9 5Z" fill="#7c9a52" />
      <g fill="#efc948" stroke="#b78a2d" strokeWidth=".7">
        {[[16, 7], [8, 13], [24, 8]].map(([x, y]) => <g key={x} transform={`translate(${x} ${y})`}>
          <path d="M0 0c-6-1-5-6-1-5 2 0 2 3 1 5Zm0 0c1-6 6-5 5-1 0 2-3 2-5 1Zm0 0c6 1 5 6 1 5-2 0-2-3-1-5Zm0 0c-1 6-6 5-5 1 0-2 3-2 5-1Z" />
          <circle r="1.2" fill="#997033" />
        </g>)}
      </g>
    </g>
  )
}

function Peas() {
  return (
    <g stroke="#476b39" strokeWidth="1">
      <path d="M6 25C7 14 15 7 27 5c-1 12-9 22-21 20Z" fill="#7ca551" />
      <path d="M7 25C12 16 19 10 27 5c-5 13-11 18-20 20Z" fill="#d4dda1" />
      <path d="M26 6c1-3 4-2 3-4M6 25l-3 3" fill="none" />
      <g fill="#91b761"><circle cx="12" cy="21" r="3" /><circle cx="17" cy="16" r="3.2" /><circle cx="22" cy="11" r="2.8" /></g>
      <path d="m10 20 1-1m4-4 1-1m4-4 1-1" stroke="#d8e7aa" />
    </g>
  )
}

function SugarBeet() {
  return (
    <g strokeWidth="1">
      <path d="M16 16C9 11 8 5 10 2c5 1 7 6 6 14Z" fill="#73974e" stroke="#466a39" />
      <path d="M16 16c0-8 4-12 9-12 1 6-3 10-9 12ZM15 16C7 14 3 10 4 6c6 0 10 4 11 10Z" fill="#8eaa5c" stroke="#466a39" />
      <path d="M16 17 11 5m5 12 6-10m-6 10L7 9" fill="none" stroke="#887040" />
      <path d="M10 18c1-4 10-4 12 0 2 5-3 9-6 11-3-2-8-6-6-11Z" fill="#e8dcc1" stroke="#aa9474" />
      <path d="M16 28c0 2 1 3 3 3m-7-11 3 1m-1 3 3 1m2-5 2-1" fill="none" stroke="#bca282" />
    </g>
  )
}

function Potato() {
  return (
    <g stroke="#91734f" strokeWidth="1">
      <path d="M14 15c-4-6-11-5-12 1-2 7 5 12 11 10 5-2 4-7 1-11Z" fill="#caa475" />
      <path d="M17 12c3-4 10-3 12 2 3 7-2 15-9 15-6 0-9-6-7-11 1-3 2-4 4-6Z" fill="#dec090" />
      <path d="m6 17 1 1m2 4 1-1m9-5 1 1m3 7 1-1m-8 0 1 1m7-7 1 1" fill="none" stroke="#99754d" strokeWidth="1.3" />
      <path d="M21 12c-1-5-5-7-9-7 0 4 4 7 9 7Zm0-2c0-5 3-7 6-8 1 4-2 7-6 8Z" fill="#7f9c56" stroke="#506f3c" />
    </g>
  )
}

function Oats() {
  return (
    <g stroke="#9b803e" strokeWidth=".9">
      <path d="M16 30V3m0 6L8 13m8-2 9 5m-9 0L6 21m10-3 9 6" fill="none" strokeWidth="1.2" />
      <g fill="#dbbd74">
        <path d="M16 2c-3 3-3 6 0 8 3-3 3-5 0-8ZM8 11c-4 2-5 6-3 9 4-1 5-5 3-9ZM6 19c-4 2-5 6-2 9 4-2 4-6 2-9Z" />
      </g>
      <g fill="#ebcf90">
        <path d="M24 14c4 2 5 5 3 9-4-1-5-5-3-9Zm0 8c4 1 6 4 4 8-4-1-6-4-4-8Z" />
      </g>
      <path d="m6 18 1-4m19 7-1-4m-21 9 1-4m22 6-2-4" fill="none" stroke="#bd9c55" />
    </g>
  )
}

function Millet() {
  return (
    <g stroke="#897340" strokeWidth=".85">
      <path d="M13 31c0-8 2-17 7-26" fill="none" strokeWidth="1.4" />
      <path d="M15 25c-6-2-9-5-10-9 6 2 9 5 10 9Zm-1 4c4-5 8-7 13-8-3 5-7 7-13 8Z" fill="#84934b" stroke="#5f743c" />
      <path d="M20 3c5 2 5 8 2 13-3 6-9 8-11 5-2-2 2-5 3-9 1-5 2-8 6-9Z" fill="#c7a45c" />
      <g fill="#e7c982">
        {[[19,6],[22,8],[17,9],[20,11],[15,12],[18,14],[21,14],[14,16],[17,18],[12,19]].map(([cx,cy]) => <ellipse key={`${cx}-${cy}`} cx={cx} cy={cy} rx="1.5" ry="1.8" />)}
      </g>
    </g>
  )
}
