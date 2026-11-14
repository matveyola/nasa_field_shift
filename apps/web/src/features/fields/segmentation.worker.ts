import { segmentField, type FieldSegmentationInput } from './segmentationEngine'

self.addEventListener('message', (event: MessageEvent<FieldSegmentationInput>) => {
  self.postMessage(segmentField(event.data))
})
