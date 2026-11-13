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
const count = ref(0)
const busy = ref(false)
const ready = ref(false)
const error = ref('')
const width = ref(0)
const height = ref(0)
const scale = ref(1)
const highlightRects = ref<Array<{ left: number; top: number; width: number; height: number }>>([])
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
    highlightRects.value = evidenceRects(
      text.items as PdfTextItem[],
      viewport,
      pdfjs,
      props.quote || '',
    )
    await new pdfjs.TextLayer({
      textContentSource: text,
      container: textHost.value,
      viewport,
    }).render()
    if (current !== epoch) return
    ready.value = true
    scroll.value.scrollTop = 0
    markEvidence()
    if (highlightRects.value[0]) {
      scroll.value.scrollTop = Math.max(
        0,
        highlightRects.value[0].top - scroll.value.clientHeight / 2 + 24,
      )
    }
  } catch (e) {
    if (current === epoch && !(e instanceof Error && e.name === 'RenderingCancelledException'))
      error.value = 'Не удалось открыть страницу PDF.'
  } finally {
    if (current === epoch) busy.value = false
  }
}

type PdfTextItem = { str?: string; transform?: number[]; width?: number; height?: number }

function evidenceRects(
  items: PdfTextItem[],
  viewport: { transform: number[]; scale: number },
  pdfjs: any,
  quote: string,
) {
  const normalized = quote.replace(/\s+/g, ' ').trim().toLocaleLowerCase('ru-RU')
  if (!normalized) return []
  const numbers = [...new Set(normalized.match(/\d+(?:[.,]\d+)?/g) || [])].filter(
    (item) => item.length >= 2,
  )
  const needles = normalized.length <= 120 ? [normalized, ...numbers] : numbers.slice(0, 24)
  const rects = []
  for (const item of items) {
    if (!item.str || !item.transform || item.width == null || item.height == null) continue
    const text = item.str.replace(/\s+/g, ' ').trim().toLocaleLowerCase('ru-RU')
    if (!text) continue
    if (
      !needles.some(
        (needle) =>
          text.includes(needle) ||
          (needle.length >= 4 && needle.includes(text) && text.length >= 3),
      )
    )
      continue
    const tx = pdfjs.Util.transform(viewport.transform, item.transform)
    const angle = Math.atan2(tx[1], tx[0])
    if (Math.abs(angle) > 0.02) continue
    const fontHeight = Math.hypot(tx[2], tx[3])
    rects.push({
      left: tx[4],
      top: tx[5] - fontHeight,
      width: item.width * viewport.scale,
      height: fontHeight,
    })
  }
  return rects
}

function markEvidence() {
  const host = textHost.value
  if (!host) return
  host
    .querySelectorAll('.pdf-evidence-hit')
    .forEach((el) => el.classList.remove('pdf-evidence-hit'))
  const quote = (props.quote || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase('ru-RU')
  if (!quote) return
  const numbers = [...new Set(quote.match(/\d+(?:[.,]\d+)?/g) || [])].filter(
    (item) => item.length >= 2,
  )
  const needles = quote.length <= 120 ? [quote, ...numbers] : numbers.slice(0, 24)
  let first: HTMLElement | null = null
  host.querySelectorAll<HTMLElement>('span').forEach((span) => {
    const text = (span.textContent || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase('ru-RU')
    if (!text) return
    const hit = needles.some(
      (needle) =>
        text.includes(needle) || (needle.length >= 4 && needle.includes(text) && text.length >= 3),
    )
    if (!hit) return
    span.classList.add('pdf-evidence-hit')
    first ??= span
  })
  first?.scrollIntoView({ block: 'center', inline: 'nearest' })
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
watch(() => props.quote, markEvidence)
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
        @click="zoom = Math.max(0.5, zoom - 0.5)"
      /><UButton
        variant="outline"
        color="neutral"
        icon="i-lucide-zoom-in"
        aria-label="Увеличить PDF"
        @click="zoom = Math.min(4, zoom + 0.5)"
      /><UButton variant="ghost" color="neutral" @click="zoom = 1">По ширине</UButton
      ><span v-if="busy && !ready" class="muted" role="status">Открываем…</span>
    </div>
    <div v-if="error" class="error-box" role="alert">
      {{ error }} <UButton @click="open">Повторить</UButton>
    </div>
    <div ref="scroll" class="pdf-scroll">
      <div
        class="pdf-stage"
        :class="{ 'pdf-stage-evidence': quote && !regionRects.length && !highlightRects.length }"
        :style="{ width: width + 'px', height: height + 'px', '--scale-factor': scale }"
      >
        <canvas ref="canvas" :style="{ width: width + 'px', height: height + 'px' }"></canvas>
        <div ref="textHost" class="textLayer"></div>
        <div class="evidenceLayer" aria-hidden="true">
          <span
            v-for="(rect, index) in regionRects.length ? regionRects : highlightRects"
            :key="index"
            class="evidence-mark"
            :style="{
              left: rect.left + 'px',
              top: rect.top + 'px',
              width: rect.width + 'px',
              height: rect.height + 'px',
            }"
          />
        </div>
        <div
          v-if="quote && !regionRects.length && !highlightRects.length"
          class="evidence-page-badge"
          role="status"
        >
          Подтверждение на этой странице
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
  overflow-y: auto;
  overflow-x: hidden;
  flex: 1;
  min-height: 100px;
  background: var(--ui-bg-muted);
  padding: 12px;
}
.pdf-stage {
  position: relative;
  box-sizing: content-box;
  margin: 0 auto;
  max-width: 100%;
  overflow: hidden;
  background: var(--ui-bg);
  border: 1px solid var(--ui-border);
  --user-unit: 1;
  --total-scale-factor: var(--scale-factor);
  --min-font-size: 1;
  --scale-round-x: 1px;
  --scale-round-y: 1px;
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
  border: 1px solid var(--ui-warning);
  border-radius: 2px;
  background: color-mix(in srgb, var(--ui-warning) 42%, transparent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--ui-warning) 22%, transparent);
  animation: pdf-evidence-pulse 1.1s ease-in-out 2;
}
@keyframes pdf-evidence-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--ui-warning) 22%, transparent);
  }
  50% {
    box-shadow: 0 0 0 6px color-mix(in srgb, var(--ui-warning) 30%, transparent);
  }
}
.textLayer :deep(.pdf-evidence-hit) {
  background: color-mix(in srgb, var(--ui-warning) 42%, transparent);
  border-radius: 2px;
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--ui-warning) 75%, transparent);
  animation: pdf-evidence-pulse 1.1s ease-in-out 2;
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
.pdf-stage-evidence {
  border-color: var(--ui-warning);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--ui-warning) 30%, transparent);
}
.evidence-page-badge {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 4;
  padding: 4px 8px;
  border: 1px solid var(--ui-warning);
  border-radius: 6px;
  color: var(--ui-warning);
  background: color-mix(in srgb, var(--ui-bg) 92%, var(--ui-warning));
  font-size: 12px;
  font-weight: 600;
}
.page-input {
  width: 72px;
}
</style>
