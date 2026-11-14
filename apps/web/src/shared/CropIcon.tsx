type CropKind = 'maize' | 'soy' | 'wheat'

const cropAliases: ReadonlyMap<string, CropKind> = new Map([
  ['maize', 'maize'], ['corn', 'maize'], ['кукурудза', 'maize'],
  ['soy', 'soy'], ['soybean', 'soy'], ['soybeans', 'soy'], ['соя', 'soy'],
  ['wheat', 'wheat'], ['spring wheat', 'wheat'], ['пшениця', 'wheat'], ['яра пшениця', 'wheat'],
])

export function CropIcon({ crop }: { crop?: string }) {
  const kind = cropAliases.get((crop ?? '').trim().toLowerCase())
  return (
    <svg className="crop-icon" viewBox="0 0 32 32" width="24" height="24"
      aria-hidden="true" focusable="false" strokeLinejoin="round" strokeLinecap="round">
      {kind === 'wheat' ? <Wheat /> : kind === 'maize' ? <Maize /> : kind === 'soy' ? <Soy /> : (
        <g fill="#d5dfc0" stroke="#59734b" strokeWidth="1.4">
          <path d="M16 26c0-7 .3-10 3-13M16 20c-1.2-5-5-6.8-9-7 .4 5 3.4 7.7 9 7Z" />
          <path d="M17 17c.2-5 3-8 8-9-.1 5-3 8.2-8 9Z" />
        </g>
      )}
    </svg>
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
