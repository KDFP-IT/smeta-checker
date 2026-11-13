<script setup lang="ts">
import type { PDFDocumentProxy, PDFDocumentLoadingTask, RenderTask } from 'pdfjs-dist'
import { clampPage } from '~/utils/presentation'

const props = defineProps<{
  sourceUrl: string
  page: number
  quote?: string
  region?: {
    width: number
    height: number
    highlights: Array<{ bbox: number[]; text: string }>
  } | null
}>()
const emit = defineEmits<{ page: [page: number]; loaded: [count: number] }>()
const canvas = ref<HTMLCanvasElement>()
const textHost = ref<HTMLElement>()
const scroll = ref<HTMLElement>()
const zoom = ref(1)
const dragging = ref(false)
let dragStart: { x: number; y: number; left: number; top: number; pointerId: number } | null = null
let zoomAnchor: { x: number; y: number; clientX: number; clientY: number } | null = null
const count = ref(0)
const busy = ref(false)
const ready = ref(false)
const error = ref('')
const width = ref(0)
const height = ref(0)
const scale = ref(1)
const evidenceLabel = computed(() => (props.quote || '').match(/\d+(?:[.,]\d+)?/)?.[0] || '')
const regionRects = computed(() => {
  const region = props.region
  if (!region || !region.width || !region.height || !width.value || !height.value) return []
  const sx = width.value / region.width
  const sy = height.value / region.height
  return (region.highlights || []).flatMap((item) => {
    if (item.bbox.length !== 4) return []
    const [x0, y0, x1, y1] = item.bbox
    return [{ left: x0 * sx, top: y0 * sy, width: (x1 - x0) * sx, height: (y1 - y0) * sy }]
  })
})
let documentPdf: PDFDocumentProxy | null = null
let loading: PDFDocumentLoadingTask | null = null
let renderTask: RenderTask | null = null
let epoch = 0
let resizeObserver: ResizeObserver | undefined

function clampZoom(value: number) {
  return Math.min(4, Math.max(0.5, value))
}

function setZoom(value: number, clientX?: number, clientY?: number) {
  const next = clampZoom(value)
  if (!scroll.value || next === zoom.value) return
  const rect = scroll.value.getBoundingClientRect()
  const x =
    (scroll.value.scrollLeft + (clientX ?? rect.left + rect.width / 2) - rect.left) /
    scroll.value.scrollWidth
  const y =
    (scroll.value.scrollTop + (clientY ?? rect.top + rect.height / 2) - rect.top) /
    scroll.value.scrollHeight
  zoomAnchor = {
    x,
    y,
    clientX: clientX ?? rect.left + rect.width / 2,
    clientY: clientY ?? rect.top + rect.height / 2,
  }
  zoom.value = next
}

function handleWheel(event: WheelEvent) {
  if (!event.ctrlKey) return
  event.preventDefault()
  setZoom(zoom.value + (event.deltaY < 0 ? 0.25 : -0.25), event.clientX, event.clientY)
}

function startDrag(event: PointerEvent) {
  if (event.button !== 0 || !scroll.value) return
  const target = event.target as HTMLElement
  if (target.closest('button, input, a, textarea, select')) return
  dragStart = {
    x: event.clientX,
    y: event.clientY,
    left: scroll.value.scrollLeft,
    top: scroll.value.scrollTop,
    pointerId: event.pointerId,
  }
  dragging.value = true
  event.preventDefault()
  try {
    scroll.value.setPointerCapture(event.pointerId)
  } catch {
    // Pointer capture is best-effort; pointermove still works on the element.
  }
}

function moveDrag(event: PointerEvent) {
  if (!dragStart || !scroll.value || dragStart.pointerId !== event.pointerId) return
  scroll.value.scrollLeft = dragStart.left - (event.clientX - dragStart.x)
  scroll.value.scrollTop = dragStart.top - (event.clientY - dragStart.y)
}

function endDrag(event: PointerEvent) {
  if (!dragStart || dragStart.pointerId !== event.pointerId) return
  dragging.value = false
  dragStart = null
  try {
    scroll.value?.releasePointerCapture(event.pointerId)
  } catch {
    // Already released by the browser.
  }
}

async function render() {
  if (!documentPdf || !canvas.value || !textHost.value || !scroll.value) return
  const current = ++epoch
  renderTask?.cancel()
  busy.value = true
  error.value = ''
  try {
    const page = await documentPdf.getPage(clampPage(props.page, documentPdf.numPages))
    if (current !== epoch) return
    const pdfjs = await import('pdfjs-dist')
    const fit = Math.max(200, scroll.value.clientWidth - 24) / page.getViewport({ scale: 1 }).width
    const viewport = page.getViewport({ scale: Math.min(fit * zoom.value, 4) })
    const ratio = Math.min(window.devicePixelRatio || 1, 2)
    width.value = viewport.width
    height.value = viewport.height
    scale.value = viewport.scale
    await nextTick()
    if (current !== epoch || !canvas.value || !textHost.value) return
    canvas.value.width = Math.ceil(viewport.width * ratio)
    canvas.value.height = Math.ceil(viewport.height * ratio)
    renderTask = page.render({
      canvas: canvas.value,
      canvasContext: canvas.value.getContext('2d')!,
      viewport,
      transform: [ratio, 0, 0, ratio, 0, 0],
    })
    await renderTask.promise
    if (current !== epoch) return
    textHost.value.innerHTML = ''
    const text = await page.getTextContent()
    await new pdfjs.TextLayer({
      textContentSource: text,
      container: textHost.value,
      viewport,
    }).render()
    if (current !== epoch) return
    ready.value = true
    if (zoomAnchor) {
      const rect = scroll.value.getBoundingClientRect()
      scroll.value.scrollLeft = Math.max(
        0,
        zoomAnchor.x * scroll.value.scrollWidth - (zoomAnchor.clientX - rect.left),
      )
      scroll.value.scrollTop = Math.max(
        0,
        zoomAnchor.y * scroll.value.scrollHeight - (zoomAnchor.clientY - rect.top),
      )
      zoomAnchor = null
    } else {
      scroll.value.scrollTop = 0
    }
  } catch (e) {
    if (current === epoch && !(e instanceof Error && e.name === 'RenderingCancelledException'))
      error.value = 'Не удалось открыть страницу PDF.'
  } finally {
    if (current === epoch) busy.value = false
  }
}

async function open() {
  epoch++
  renderTask?.cancel()
  if (loading) await loading.destroy()
  documentPdf = null
  ready.value = false
  busy.value = true
  error.value = ''
  try {
    const pdfjs = await import('pdfjs-dist')
    pdfjs.GlobalWorkerOptions.workerSrc = '/pdfjs/build/pdf.worker.mjs'
    const task = pdfjs.getDocument({
      url: props.sourceUrl,
      cMapUrl: '/pdfjs/cmaps/',
      cMapPacked: true,
      standardFontDataUrl: '/pdfjs/standard_fonts/',
      wasmUrl: '/pdfjs/wasm/',
    })
    loading = task
    const pdf = await task.promise
    if (task !== loading) {
      await task.destroy()
      return
    }
    documentPdf = pdf
    count.value = pdf.numPages
    emit('loaded', pdf.numPages)
    await render()
  } catch {
    error.value = 'Не удалось загрузить PDF. Проверьте доступность документа.'
    busy.value = false
  }
}

function changePage(value: number) {
  emit('page', clampPage(value, count.value))
}

watch(() => props.sourceUrl, open)
watch(() => props.page, render)
watch(() => props.quote, render)
watch(zoom, render)
onMounted(() => {
  open()
  let timer: ReturnType<typeof setTimeout>
  resizeObserver = new ResizeObserver(() => {
    clearTimeout(timer)
    timer = setTimeout(render, 150)
  })
  if (scroll.value) resizeObserver.observe(scroll.value)
})
onBeforeUnmount(() => {
  epoch++
  resizeObserver?.disconnect()
  renderTask?.cancel()
  loading?.destroy()
  loading = null
  documentPdf = null
})

async function find(query: string) {
  const value = query.trim().toLocaleLowerCase('ru-RU')
  const hits: number[] = []
  const doc = documentPdf
  if (!doc || !value) return hits
  for (let n = 1; n <= doc.numPages; n++) {
    if (doc !== documentPdf) return []
    const content = await (await doc.getPage(n)).getTextContent()
    if (
      content.items
        .map((item) => ('str' in item ? item.str : ''))
        .join(' ')
        .toLocaleLowerCase('ru-RU')
        .includes(value)
    )
      hits.push(n)
  }
  if (hits[0]) emit('page', hits[0])
  return hits
}

defineExpose({ find })
</script>

<template>
  <div class="pdf-container">
    <div class="toolbar pdf-toolbar">
      <UButton
        variant="outline"
        color="neutral"
        icon="i-lucide-chevron-left"
        aria-label="Предыдущая страница PDF"
        :disabled="page <= 1"
        @click="changePage(page - 1)"
      /><UInput
        type="number"
        :model-value="page"
        :min="1"
        :max="count"
        aria-label="Физическая страница PDF"
        class="page-input"
        @change="changePage(Number(($event.target as HTMLInputElement).value))"
      /><span class="muted pdf-count">из {{ count || '…' }}</span
      ><UButton
        variant="outline"
        color="neutral"
        icon="i-lucide-chevron-right"
        aria-label="Следующая страница PDF"
        :disabled="page >= count"
        @click="changePage(page + 1)"
      /><UButton
        variant="outline"
        color="neutral"
        icon="i-lucide-zoom-out"
        aria-label="Уменьшить PDF"
        @click="setZoom(zoom - 0.5)"
      /><UButton
        variant="outline"
        color="neutral"
        icon="i-lucide-zoom-in"
        aria-label="Увеличить PDF"
        @click="setZoom(zoom + 0.5)"
      /><UButton variant="ghost" color="neutral" @click="setZoom(1)">По ширине</UButton
      ><span v-if="busy && !ready" class="muted" role="status">Открываем…</span>
    </div>
    <div v-if="error" class="error-box" role="alert">
      {{ error }} <UButton @click="open">Повторить</UButton>
    </div>
    <div
      ref="scroll"
      class="pdf-scroll"
      :class="{ 'is-dragging': dragging }"
      @wheel="handleWheel"
      @pointerdown="startDrag"
      @pointermove="moveDrag"
      @pointerup="endDrag"
      @pointercancel="endDrag"
    >
      <div
        class="pdf-stage"
        :style="{ width: width + 'px', height: height + 'px', '--scale-factor': scale }"
      >
        <canvas ref="canvas" :style="{ width: width + 'px', height: height + 'px' }"></canvas>
        <div ref="textHost" class="textLayer"></div>
        <div class="evidenceLayer" aria-hidden="true">
          <span
            v-for="(rect, index) in regionRects"
            :key="index"
            class="evidence-mark"
            :style="{
              left: rect.left + 'px',
              top: rect.top + 'px',
              width: rect.width + 'px',
              height: rect.height + 'px',
            }"
            ><span v-if="index === 0 && evidenceLabel" class="evidence-label">{{
              evidenceLabel
            }}</span></span
          >
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pdf-container {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.pdf-toolbar {
  min-height: 44px;
  font-size: 14px;
}
.pdf-count {
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}
.pdf-scroll {
  overflow: auto;
  cursor: grab;
  user-select: none;
  flex: 1;
  min-height: 100px;
  background: var(--ui-bg-muted);
  padding: 12px;
}
.pdf-stage {
  position: relative;
  box-sizing: content-box;
  margin: 0 auto;
  max-width: none;
  overflow: hidden;
  background: var(--ui-bg);
  border: 1px solid var(--ui-border);
  --user-unit: 1;
  --total-scale-factor: var(--scale-factor);
  --min-font-size: 1;
  --scale-round-x: 1px;
  --scale-round-y: 1px;
}
.pdf-scroll.is-dragging {
  cursor: grabbing;
}
.pdf-stage canvas {
  display: block;
}
.textLayer {
  position: absolute;
  inset: 0;
  overflow: hidden;
  contain: layout paint;
  line-height: 1;
  text-size-adjust: none;
  z-index: 2;
}
.textLayer :deep(:is(span, br)) {
  color: transparent;
  position: absolute;
  white-space: pre;
  cursor: text;
  transform-origin: 0 0;
}
.textLayer :deep(::selection) {
  background: color-mix(in srgb, var(--ui-info) 35%, transparent);
}
.evidenceLayer {
  position: absolute;
  inset: 0;
  z-index: 3;
  pointer-events: none;
}
.evidence-mark {
  position: absolute;
  border: 2px solid var(--ui-warning);
  border-radius: 2px;
  background: color-mix(in srgb, var(--ui-warning) 42%, transparent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ui-warning) 22%, transparent);
}
.evidence-label {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 5px);
  transform: translateX(-50%);
  padding: 1px 5px;
  border: 1px solid var(--ui-warning);
  border-radius: 4px;
  color: var(--ui-warning);
  background: var(--ui-bg);
  font-size: 11px;
  line-height: 1.3;
  white-space: nowrap;
}
.textLayer :deep(.pdf-evidence-hit) {
  background: color-mix(in srgb, var(--ui-warning) 42%, transparent);
  border-radius: 2px;
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--ui-warning) 75%, transparent);
}
@keyframes pdf-evidence-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--ui-warning) 55%, transparent);
  }
  50% {
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--ui-warning) 35%, transparent);
  }
}
.page-input {
  width: 72px;
}
</style>
