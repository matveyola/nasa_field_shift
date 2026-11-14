const maximumPhotoBytes = 512 * 1024
const maximumPhotoEdge = 1600

export async function prepareScoutingPhoto(file: File): Promise<string> {
  if (file.size > 20 * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
    throw new Error('Choose a JPEG, PNG or WebP photo up to 20 MB.')
  }

  const image = await createImageBitmap(file).catch(() => {
    throw new Error('This photo could not be opened.')
  })
  const canvas = document.createElement('canvas')
  try {
    const scale = Math.min(1, maximumPhotoEdge / Math.max(image.width, image.height))
    canvas.width = Math.max(1, Math.round(image.width * scale))
    canvas.height = Math.max(1, Math.round(image.height * scale))
    const context = canvas.getContext('2d', { alpha: false })
    if (!context) throw new Error('This photo could not be opened.')
    context.fillStyle = '#fff'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    for (const quality of [0.85, 0.65, 0.45, 0.25]) {
      const jpeg = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality))
      if (jpeg?.type === 'image/jpeg' && jpeg.size <= maximumPhotoBytes) {
        const bytes = new Uint8Array(await jpeg.arrayBuffer())
        let binary = ''
        for (let offset = 0; offset < bytes.length; offset += 16_384) {
          binary += String.fromCharCode(...bytes.subarray(offset, offset + 16_384))
        }
        return btoa(binary)
      }
    }
    throw new Error('This photo is too detailed. Try a smaller image.')
  } finally {
    image.close()
    canvas.width = canvas.height = 0
  }
}
